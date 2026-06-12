"""Unit tests for the V3 sentence/clause chunker (T05, D-V3-2).

Covers the locked D-V3-2 strategy: first-chunk-shorter at clause
delimiters, subsequent sentence-level chunks with a min-length floor +
merge-forward, the lookahead guard, max-chars hard split, force-emit, and
flush-on-end / discard-on-cancel. The mis-segmentation corpus
(D-V3-X-sentence-tokenizer) is exercised here at unit level — the rule-based
splitter must not break inside abbreviations / decimals / initials; T14 is
the live operator-pass gate where pysbd is the named falsification upgrade.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from persona_voice.tts import StreamingTTSConfig
from persona_voice.tts.chunking import TextChunker, chunk_text_stream


def _chunker(**overrides: int) -> TextChunker:
    params: dict[str, int] = {
        "min_first_chars": 10,
        "max_first_words": 30,
        "min_chars": 20,
        "max_chars": 300,
    }
    params.update(overrides)
    return TextChunker(**params)


def _subseq_chunker(**overrides: int) -> TextChunker:
    """A chunker already past the first chunk, with an empty buffer.

    Primes ``_first_emitted`` then ``flush()`` clears the residual so
    subsequent-chunk tests start from a clean sentence-mode state (the
    first chunk uses the shorter clause-level policy, which would
    otherwise leave residue interleaved into the assertions).
    """
    c = _chunker(**overrides)
    c.push("Priming opening sentence here, done well. ")
    c.flush()
    return c


# ---------- first chunk (clause-level, shorter) ----------------------------


def test_first_chunk_emits_at_clause_delimiter() -> None:
    c = _chunker()
    out = c.push("Honestly speaking, that is interesting.")
    # First chunk emits at the comma (clause delimiter) once >= 10 chars.
    assert out[0] == "Honestly speaking,"


def test_first_chunk_force_emits_after_max_words() -> None:
    c = _chunker(max_first_words=5)
    # No clause/sentence delimiter; force-emit after 5 words once a 6th
    # word starts.
    out = c.push("one two three four five six seven eight")
    assert len(out) == 1
    assert out[0].split() == ["one", "two", "three", "four", "five"]


def test_first_chunk_waits_below_min_first_chars() -> None:
    c = _chunker(min_first_chars=10)
    # "Hi," is below the 10-char floor — boundary skipped, keep buffering.
    assert c.push("Hi, ") == []


# ---------- subsequent chunks (sentence-level) -----------------------------


def test_subsequent_chunks_emit_at_sentence_enders() -> None:
    c = _subseq_chunker()
    out = c.push("This is the second full sentence. And a third one follows.")
    # Sentence-level chunks (>= 20 chars) emit on the confirmed boundary.
    assert "This is the second full sentence." in out


def test_short_sentence_merges_forward() -> None:
    c = _subseq_chunker()
    # "Hi." is below the 20-char floor — merges into the following sentence
    # rather than emitting as a robotic fragment.
    out = c.push("Hi. This continues into a long sentence here.")
    out += c.flush()
    assert any(chunk.startswith("Hi.") and len(chunk) >= 20 for chunk in out)


# ---------- lookahead guard ------------------------------------------------


def test_lookahead_guard_holds_boundary_at_buffer_end() -> None:
    c = _subseq_chunker()
    # A period at the very end is NOT a confirmed boundary yet.
    assert c.push("Here is a full sentence ending now.") == []
    # The next non-whitespace token confirms it.
    out = c.push(" Next.")
    assert out
    assert out[0] == "Here is a full sentence ending now."


# ---------- mis-segmentation corpus (D-V3-X-sentence-tokenizer) ------------


@pytest.mark.parametrize(
    "text",
    [
        "Dr. Smith arrived early today and sat down quietly.",
        "Pi is roughly 3.14159 by any sane measure of things.",
        "We met at 9 a.m. sharp to discuss the whole proposal.",
        "The U.S. economy shifted a great deal over the decade.",
        "Use e.g. this approach when you need a longer example.",
    ],
)
def test_no_split_inside_abbreviation_or_decimal(text: str) -> None:
    # The interior abbreviation/decimal period must NOT split; the ONLY
    # boundary is the terminal period (unconfirmed at end → emitted by flush).
    c = _chunker()
    out = c.push(text)
    out += c.flush()
    assert out == [text]


def test_decimal_not_split_but_terminal_period_is() -> None:
    c = _chunker()
    out = c.push("It costs 29.99 dollars total. Next item please now.")
    out += c.flush()
    assert "It costs 29.99 dollars total." in out
    assert "Next item please now." in out


# ---------- max-chars hard split -------------------------------------------


def test_hard_split_at_max_chars_on_whitespace() -> None:
    c = _chunker(max_chars=40)
    c.push("A priming opening clause here, yes indeed. ")  # first chunk gone
    long_run = "word " * 30  # no sentence ender, far over 40 chars
    out = c.push(long_run)
    assert out  # forced a hard split rather than buffering unboundedly
    assert all(len(chunk) <= 40 for chunk in out)


# ---------- flush / discard-on-cancel --------------------------------------


def test_flush_emits_unterminated_residual() -> None:
    c = _subseq_chunker()
    c.push("Trailing reply with no terminal punctuation")
    assert c.flush() == ["Trailing reply with no terminal punctuation"]


def test_discard_on_cancel_does_not_emit_buffer() -> None:
    # Barge-in: the caller stops iterating and never calls flush — the
    # in-flight buffer is discarded, never synthesised (D-V3-2).
    c = _chunker()
    emitted = c.push("Half a sentence that never")
    assert emitted == []  # nothing confirmed; flush NOT called → discarded


# ---------- async driver ---------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_text_stream_first_chunk_before_completion() -> None:
    # Criterion #2 shape at the chunker layer: the first chunk is yielded
    # before the token stream is exhausted.
    config = StreamingTTSConfig(api_key="k")
    tokens = [
        "The quick brown fox, ",
        "having paused, ",
        "jumped over the lazy dog. ",
        "Then it ran away fast.",
    ]

    async def _gen() -> AsyncIterator[str]:
        for t in tokens:
            yield t

    chunks = [c async for c in chunk_text_stream(_gen(), config)]
    assert chunks  # produced output
    # The first chunk is the opening clause (emitted well before the stream
    # finished generating the rest).
    assert chunks[0] == "The quick brown fox,"
    # The terminal sentence is flushed at stream end.
    assert chunks[-1] == "Then it ran away fast."


@pytest.mark.asyncio
async def test_chunk_text_stream_token_by_token() -> None:
    # Tokens may arrive one character at a time; the chunker still resolves
    # boundaries correctly across token splits.
    config = StreamingTTSConfig(api_key="k")
    text = "Hello there, world. This is a fairly long second sentence here."

    async def _gen() -> AsyncIterator[str]:
        for ch in text:
            yield ch

    chunks = [c async for c in chunk_text_stream(_gen(), config)]
    assert chunks[0] == "Hello there,"
    assert "".join(chunks).replace(" ", "").startswith("Hellothere,world.")
