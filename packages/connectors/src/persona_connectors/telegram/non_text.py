"""Non-text graceful declines (Spec C2 T4, D-C2-6) — pure product-voice copy.

When :func:`~persona_connectors.telegram.inbound.classify_update` yields an
:class:`~persona_connectors.telegram.inbound.InboundNonText`, the flow replies with
a friendly, text-only-for-now line rather than an error or silence (criterion 8).
The line is the **bot speaking** (system-level), not a persona — so it carries no
persona name tag and is sent as plain text; it never drives a runtime turn.

One consistent line per :class:`~persona_connectors.telegram.inbound.NonTextKind`,
in the product (F1) voice — warm, clear, and explicit that text is the channel
for now (voice/vision are later directions).

Pure + api-free: a total function over the enum (``assert_never`` guarantees every
kind is handled), no I/O, no ``persona_api``.
"""

from __future__ import annotations

from typing import assert_never

from persona_connectors.telegram.inbound import NonTextKind

__all__ = ["decline_message"]


def decline_message(kind: NonTextKind) -> str:
    """Return the friendly text-only decline for a non-text message (D-C2-6).

    Args:
        kind: The classified non-text content kind.

    Returns:
        A single product-voice line making clear the persona works over text for now.
    """
    match kind:
        case NonTextKind.voice:
            return "I can't listen to voice messages yet — send me a text message and I'll reply."
        case NonTextKind.media:
            return "I work over text for now — type me a message and I'm all yours."
        case NonTextKind.unknown:
            return "I work over text — send me a message and I'll reply."
        case _:  # pragma: no cover - exhaustiveness guard (mypy assert_never)
            assert_never(kind)
