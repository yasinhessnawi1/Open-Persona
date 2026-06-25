"""decline_message — friendly text-only declines (Spec C2 T4, D-C2-6).

Every NonTextKind maps to a non-empty, product-voice line that makes the text-only
posture clear — never an error or silence (criterion 8).
"""

from __future__ import annotations

import pytest
from persona_connectors.telegram.inbound import NonTextKind
from persona_connectors.telegram.non_text import decline_message


@pytest.mark.parametrize("kind", list(NonTextKind))
def test_every_kind_has_a_nonempty_friendly_decline(kind: NonTextKind) -> None:
    """Total over the enum: each kind yields a non-empty line mentioning text."""
    message = decline_message(kind)
    assert message.strip()
    assert "text" in message.lower()


def test_voice_decline_speaks_to_listening() -> None:
    """The voice decline names the specific limitation (can't listen yet)."""
    assert "voice" in decline_message(NonTextKind.voice).lower()


def test_declines_are_distinct_per_kind() -> None:
    """Voice/media/unknown each get a tailored line (not one generic catch-all)."""
    messages = {decline_message(k) for k in NonTextKind}
    assert len(messages) == len(list(NonTextKind))
