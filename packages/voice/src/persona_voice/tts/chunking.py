"""Sentence/clause text chunker for streaming TTS (T05, D-V3-2).

Turns an ``AsyncIterator[str]`` of LLM reply tokens into an
``AsyncIterator[str]`` of prosody-coherent text chunks the TTS backend
synthesises. The central design tension (spec §3): token-level chunking
is lowest-latency / worst-prosody; full-reply is best-prosody /
worst-latency; sentence-level with a shorter first chunk is the locked
sweet spot (D-V3-2), convergent across six academic papers + provider
docs + four production stacks (research.md §R-V3-2 + §R-V3-5).

**Strategy (D-V3-2).**

* *First chunk* — emit at the first clause delimiter (``, ; : — \\n``)
  once ``chunk_min_first_chars`` is buffered; force-emit after
  ``chunk_max_first_words`` words if no delimiter appears. The latency
  cost of accumulation is concentrated entirely in the first chunk
  (research.md §R-V3-2 §3), so a shorter first chunk is the production
  default — not a hack.
* *Subsequent chunks* — emit at sentence enders only (``. ! ? … \\n``),
  with a ``chunk_min_chars`` floor (short sentences merge forward,
  LiveKit pattern) and a ``chunk_max_chars`` hard split at whitespace
  (also bounds V4 barge-in stop resolution).
* *Lookahead guard (non-negotiable)* — a boundary candidate at the end of
  the buffer is never confirmed; at least one non-whitespace char must
  follow the punctuation (disambiguates ``"$29."`` mid-decimal vs
  ``"$29. Next"``). The single most replicated chunker detail across
  LiveKit + Pipecat source.
* *Flush-on-stream-end (mandatory)* — when the token stream exhausts,
  :meth:`TextChunker.flush` emits the residual buffer regardless of
  boundary state (a reply ending without terminal punctuation is still
  spoken). On barge-in the caller simply stops iterating and does NOT
  call ``flush`` — the buffer is discarded, never synthesised
  (D-V3-2 discard-on-cancel).

**Rule-based tokenizer (D-V3-X-sentence-tokenizer RULING).** Boundary
detection is a dependency-free punctuation-set + min-length + lookahead
splitter with abbreviation/decimal/initial protection — NOT pysbd or
BlingFire (the project's "can I write this in 50 lines instead?"
dependency discipline, ENGINEERING_STANDARDS §5). pysbd is the *named*
falsification upgrade: if the T14 mis-segmentation corpus fails on this
splitter, swap pysbd behind this same interface (one module, no Protocol
change). Our input is LLM-generated prose (cleaner than the adversarial
Golden Rule Set), so the rule-based path covers the high-frequency
failures (``Dr.`` / ``3.14`` / ``e.g.`` / ``$29.``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.tts.config import StreamingTTSConfig

__all__ = ["TextChunker", "chunk_text_stream"]

_SENTENCE_ENDERS = ".!?…\n"
_CLAUSE_DELIMS = ",;:—\n"

# Lowercased abbreviation tokens (without the trailing period) whose period
# is NOT a sentence boundary. Internal-dot forms ("e.g") are matched after
# scanning back over the [alpha + dot] run, so both periods of "e.g." are
# protected. Extensible per D-V3-X-sentence-tokenizer.
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "no",
        "fig",
        "approx",
        "dept",
        "est",
        "gen",
        "gov",
        "lt",
        "col",
        "sgt",
        "capt",
        "rev",
        "hon",
        "e.g",
        "i.e",
        "a.m",
        "p.m",
        "u.s",
        "u.k",
        "ph.d",
    }
)


def _is_protected_period(buf: str, i: int) -> bool:
    """Return ``True`` if the ``.`` at ``buf[i]`` is NOT a sentence boundary.

    Covers the three high-frequency false-boundary classes (research.md
    §R-V3-2): decimals (``3.14``), single-letter initials (``J. R. R.``),
    and known abbreviations (``Dr.`` / ``e.g.``).
    """
    prev = buf[i - 1] if i > 0 else ""
    nxt = buf[i + 1] if i + 1 < len(buf) else ""
    # Decimal: digit on both sides.
    if prev.isdigit() and nxt.isdigit():
        return True
    # Single-letter initial: one alpha char preceded by a non-alphanumeric
    # boundary (start-of-buffer, space, open paren, ...).
    if prev.isalpha() and (i - 1 == 0 or not buf[i - 2].isalnum()):
        return True
    # Abbreviation: scan back over the maximal [alpha + dot] run and match.
    start = i
    while start > 0 and (buf[start - 1].isalpha() or buf[start - 1] == "."):
        start -= 1
    word = buf[start:i].lower().strip(".")
    return word in _ABBREVIATIONS


class TextChunker:
    """Stateful, deterministic text chunker — fed text, yields chunks.

    Deliberately synchronous + clockless so unit tests need no timing
    (D-V3-X-no-pacing-t06 sibling discipline). The async driver
    :func:`chunk_text_stream` wraps it for the V5 token-stream path.
    """

    def __init__(
        self,
        *,
        min_first_chars: int,
        max_first_words: int,
        min_chars: int,
        max_chars: int,
    ) -> None:
        self._min_first_chars = min_first_chars
        self._max_first_words = max_first_words
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._buffer = ""
        self._first_emitted = False

    @classmethod
    def from_config(cls, config: StreamingTTSConfig) -> TextChunker:
        """Build a chunker from the locked D-V3-2 config knobs."""
        return cls(
            min_first_chars=config.chunk_min_first_chars,
            max_first_words=config.chunk_max_first_words,
            min_chars=config.chunk_min_chars,
            max_chars=config.chunk_max_chars,
        )

    def push(self, text: str) -> list[str]:
        """Append ``text`` and return any chunks that became emittable."""
        self._buffer += text
        out: list[str] = []
        while True:
            chunk = self._try_extract()
            if chunk is None:
                break
            if chunk:
                out.append(chunk)
        return out

    def flush(self) -> list[str]:
        """Emit the residual buffer (D-V3-2 flush-on-stream-end).

        Call ONLY at normal stream end. On barge-in do NOT call this —
        stop iterating so the buffer is discarded (D-V3-2 discard-on-cancel).
        """
        residual = self._buffer.strip()
        self._buffer = ""
        if residual:
            self._first_emitted = True
            return [residual]
        return []

    def _emit_at(self, idx: int) -> str:
        chunk = self._buffer[:idx].strip()
        self._buffer = self._buffer[idx:].lstrip()
        self._first_emitted = True
        return chunk

    def _try_extract(self) -> str | None:
        """Return the next emittable chunk, or ``None`` if more text is needed."""
        buf = self._buffer
        n = len(buf)
        if n == 0:
            return None
        floor = self._min_chars if self._first_emitted else self._min_first_chars
        delims = _SENTENCE_ENDERS if self._first_emitted else _SENTENCE_ENDERS + _CLAUSE_DELIMS

        i = 0
        while i < n:
            ch = buf[i]
            if ch in delims:
                if ch == "." and _is_protected_period(buf, i):
                    i += 1
                    continue
                # Consume a run of clustered sentence enders ("?!", "...").
                j = i + 1
                while j < n and buf[j] in _SENTENCE_ENDERS and buf[j] != "\n":
                    j += 1
                # Lookahead: a boundary at the buffer end is unconfirmed.
                k = j
                while k < n and buf[k].isspace():
                    k += 1
                if k >= n:
                    return None
                if len(buf[:j].strip()) >= floor:
                    return self._emit_at(j)
                # Too short — merge forward past this boundary.
                i = j
                continue
            i += 1

        # No confirmed boundary. Force conditions:
        # (1) First chunk: force-emit after max_first_words words.
        if not self._first_emitted:
            words = buf.split()
            if len(words) >= self._max_first_words:
                return self._emit_at(self._word_cut(buf, self._max_first_words))
        # (2) Any chunk: hard split once the buffer reaches max_chars.
        if len(buf.strip()) >= self._max_chars:
            return self._emit_at(self._hard_split(buf, self._max_chars))
        return None

    def _word_cut(self, buf: str, n_words: int) -> int:
        """Index just past the ``n_words``-th word (for first-chunk force)."""
        count = 0
        in_word = False
        for idx, ch in enumerate(buf):
            if ch.isspace():
                in_word = False
            elif not in_word:
                in_word = True
                count += 1
                if count > n_words:
                    return idx
        return len(buf)

    def _hard_split(self, buf: str, max_chars: int) -> int:
        """Last whitespace at/under ``max_chars`` (or ``max_chars`` if none)."""
        window = buf[:max_chars]
        cut = window.rfind(" ")
        return cut if cut > 0 else max_chars


async def chunk_text_stream(
    tokens: AsyncIterator[str],
    config: StreamingTTSConfig,
) -> AsyncIterator[str]:
    """Drive a :class:`TextChunker` over a V5 token stream (D-V3-2).

    Yields prosody-coherent chunks as boundaries resolve, then flushes the
    residual when ``tokens`` exhausts. On barge-in the consumer cancels
    iteration — ``flush`` is never reached, so the in-flight buffer is
    discarded (D-V3-2 discard-on-cancel).
    """
    chunker = TextChunker.from_config(config)
    async for token in tokens:
        for chunk in chunker.push(token):
            yield chunk
    for chunk in chunker.flush():
        yield chunk
