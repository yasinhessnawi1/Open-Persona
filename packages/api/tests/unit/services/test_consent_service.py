"""Unit tests for the pure auto-dispatch consent state machine — spec 21 T09.

The tri-state semantics (D-21-7) and the stable-decline rule (D-21-17) are pure
functions, tested here without a database. The DB round-trip + RLS lives in the
integration suite.
"""

from __future__ import annotations

import pytest
from persona_api.services.consent_service import (
    can_auto_dispatch,
    should_prompt_for_consent,
)


class TestCanAutoDispatch:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [(True, True), (False, False), (None, False)],
    )
    def test_only_granted_auto_dispatches(self, state: bool | None, expected: bool) -> None:
        assert can_auto_dispatch(state) is expected


class TestShouldPrompt:
    def test_unset_prompts(self) -> None:
        # None = never asked / revoked-to-ask → prompt on next autonomous dispatch.
        assert should_prompt_for_consent(None) is True

    def test_granted_does_not_prompt(self) -> None:
        assert should_prompt_for_consent(True) is False

    def test_declined_does_not_prompt(self) -> None:
        # D-21-17: an explicit decline is stable — never auto-re-prompts.
        assert should_prompt_for_consent(False) is False

    def test_grant_then_dispatch_then_no_prompt(self) -> None:
        state: bool | None = None
        assert should_prompt_for_consent(state) is True  # first task asks
        state = True  # user grants
        assert can_auto_dispatch(state) is True
        assert should_prompt_for_consent(state) is False  # subsequent tasks auto

    def test_revoke_re_arms_prompt(self) -> None:
        state: bool | None = True
        state = None  # settings toggle OFF → revoke-to-ask
        assert should_prompt_for_consent(state) is True
