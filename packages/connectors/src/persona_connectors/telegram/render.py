"""Outbound rendering + message splitting (Spec C2 T3) — pure, the C3/C4 reference.

Two pure concerns the adapter needs to turn a persona reply into Telegram
message(s):

- **Splitting (D-C2-3).** Telegram caps a message at **4096 characters "after
  entities parsing"** — i.e. the *visible* text length, counted in **UTF-16 code
  units** (an emoji / astral-plane char counts as 2; HTML tags + entities don't
  count, since they're parsed out). :func:`split_text` breaks the **plaintext** on
  natural boundaries — paragraph → line → sentence → word — **never mid-word**, and
  never mid-surrogate (it iterates code points, so an astral char is atomic); a
  single token longer than the budget is hard-wrapped only as a last resort.
- **Rendering (D-C2-5).** :func:`render_outbound` lowers C0's semantic
  :class:`~persona.schema.origination.PersonaIdentityTag` to Telegram's
  **bold-prefix** render tier (C1-D-6): a ``<b>Name</b>`` header on the **first
  part only** (continuation parts carry no header — clean, not cluttered), with the
  name and body **HTML-escaped** (``& < >``). It splits the **plaintext first, then
  wraps** each part, so an HTML tag can never be torn across a message boundary.

This module is **pure + api-free** — deterministic, no I/O, no ``persona_api``. The
boundary-splitting algorithm is the reusable reference the other text platforms
(C3/C4) follow; the UTF-16 unit count is the Telegram-specific budget (SMS/email
will supply their own measure when those adapters land).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_connectors.telegram.client import TELEGRAM_MAX_MESSAGE_CHARS

if TYPE_CHECKING:
    from persona.schema.origination import PersonaIdentityTag

__all__ = [
    "PARSE_MODE_HTML",
    "escape_html",
    "render_outbound",
    "split_text",
    "utf16_length",
]

# Telegram's HTML parse mode (D-C2-5) — escapes only ``& < >`` (3 chars), far
# safer over arbitrary persona text than MarkdownV2's ~18-char escape set.
PARSE_MODE_HTML = "HTML"


def utf16_length(text: str) -> int:
    """The length of ``text`` in UTF-16 code units (Telegram's counting unit).

    A code point above U+FFFF (emoji, some CJK) occupies two UTF-16 units, so this
    is ``>= len(text)``. This is the unit Telegram's 4096 limit is measured in.
    """
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def escape_html(text: str) -> str:
    """Escape the three characters Telegram HTML requires (``&`` first, then ``<``/``>``)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _max_prefix_len(text: str, budget: int) -> int:
    """Largest code-point index ``i`` with ``utf16_length(text[:i]) <= budget``.

    Iterating by code point means a surrogate pair is never split (an astral char
    is one element contributing 2 units atomically).
    """
    total = 0
    for index, ch in enumerate(text):
        units = 2 if ord(ch) > 0xFFFF else 1
        if total + units > budget:
            return index
        total += units
    return len(text)


def _find_break(window: str) -> int:
    """The best natural break index within ``window`` (the rightmost boundary).

    Preference: paragraph (``\\n\\n``) → line (``\\n``) → sentence
    (``. `` / ``! `` / ``? ``) → word (whitespace). Returns the index to cut at
    (everything before it is one chunk), or ``-1`` when ``window`` has no boundary
    (the caller hard-wraps).
    """
    paragraph = window.rfind("\n\n")
    if paragraph != -1:
        return paragraph + 2
    line = window.rfind("\n")
    if line != -1:
        return line + 1
    sentence = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence != -1:
        return sentence + 2  # cut after the terminator + its space
    word = max(window.rfind(" "), window.rfind("\t"))
    if word != -1:
        return word + 1
    return -1


def split_text(
    text: str, *, budget: int = TELEGRAM_MAX_MESSAGE_CHARS, first_budget: int | None = None
) -> list[str]:
    """Split ``text`` into chunks within the UTF-16 ``budget``, on natural boundaries.

    Args:
        text: The plaintext to split (markup-free — render/escape happens after).
        budget: Max UTF-16 units per chunk.
        first_budget: A smaller budget for the FIRST chunk (it reserves room for a
            name header, D-C2-5); ``None`` → use ``budget`` for every chunk.

    Returns:
        Non-empty, whitespace-trimmed chunks, each within its budget, in order. A
        chunk breaks on a paragraph/line/sentence/word boundary where one exists
        within budget; a single over-budget token is hard-wrapped as a last resort.

    Raises:
        ValueError: ``budget`` (or a provided ``first_budget``) is below 1.
    """
    if budget < 1:
        raise ValueError("budget must be >= 1")
    if first_budget is not None and first_budget < 1:
        raise ValueError("first_budget must be >= 1")

    chunks: list[str] = []
    remaining = text
    while remaining:
        cap = first_budget if (not chunks and first_budget is not None) else budget
        if utf16_length(remaining) <= cap:
            chunk, remaining = remaining, ""
        else:
            max_index = _max_prefix_len(remaining, cap) or 1  # guarantee progress
            window = remaining[:max_index]
            cut = _find_break(window)
            if cut <= 0:
                cut = max_index  # no boundary in budget → hard-wrap
            chunk, remaining = remaining[:cut], remaining[cut:]
        trimmed = chunk.strip()
        if trimmed:
            chunks.append(trimmed)
    return chunks


def render_outbound(
    persona: PersonaIdentityTag,
    text: str,
    *,
    budget: int = TELEGRAM_MAX_MESSAGE_CHARS,
) -> list[str]:
    """Render a persona reply to Telegram HTML message part(s) (D-C2-5 + D-C2-3).

    Lowers the semantic name tag to the bold-prefix tier: a ``<b>Name</b>`` header
    on the first part only, name + body HTML-escaped. Splits the **plaintext**
    against the UTF-16 budget (the first part reserving room for the header), then
    wraps each part — so a tag never tears across a message boundary.

    Args:
        persona: The originating persona's identity tag (the SEMANTIC tag, C1-D-6).
        text: The plain reply body (markup-free).
        budget: The per-message UTF-16 budget (defaults to Telegram's 4096).

    Returns:
        The ordered list of HTML message strings to send with ``parse_mode=HTML``
        (at least one — a header-only message when the body is empty).
    """
    name = persona.display_name
    header = f"<b>{escape_html(name)}</b>"
    # The header occupies ``utf16(name) + 1`` visible units (the name + the newline
    # before the body) — reserve that from the first part's budget.
    reserve = utf16_length(name) + 1
    first_budget = max(1, budget - reserve)

    body_chunks = split_text(text, budget=budget, first_budget=first_budget)
    if not body_chunks:
        return [header]
    messages: list[str] = []
    for index, chunk in enumerate(body_chunks):
        escaped = escape_html(chunk)
        messages.append(f"{header}\n{escaped}" if index == 0 else escaped)
    return messages
