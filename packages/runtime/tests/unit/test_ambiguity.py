"""Tests for ``persona_runtime.ambiguity`` — spec 21 T05 (tier-1 detector).

Covers per-class detection (A/B/C/D), the hard suppressors, deictic referent
gating, long-message windowing, class-priority resolution, the Norwegian
patterns, and the autonomy gating matrix (spec §2.1 / D-21-19 class-C-never /
class-D-always), plus the tier-2 escalator Protocol seam (D-21-1).
"""
# ruff: noqa: ARG002 — Protocol-satisfying stub deliberately ignores its args

from __future__ import annotations

import pytest
from persona.autonomy import AmbiguityClass, policy_for
from persona_runtime.ambiguity import (
    AmbiguityEscalator,
    AmbiguitySignal,
    DetectionContext,
    EscalationVerdict,
    detect_ambiguity,
    should_ask,
)

FIRST_TURN = DetectionContext()  # no prior context, no preceding question


def _signal(klass: AmbiguityClass) -> AmbiguitySignal:
    return AmbiguitySignal(
        signal_class=klass,
        pattern_id="x",
        matched_span="x",
        missing_element="x",
        weight=1.0,
        language="en",
    )


class TestDetectionPerClass:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("send", AmbiguityClass.MISSING_PARAMETER),
            ("set up a meeting", AmbiguityClass.MISSING_PARAMETER),
            ("draft a complaint", AmbiguityClass.VAGUE_SCOPE),
            ("fix this", AmbiguityClass.VAGUE_SCOPE),
            ("I want it cheap but premium quality", AmbiguityClass.CONFLICTING_CONSTRAINTS),
            ("delete everything", AmbiguityClass.SAFETY_CRITICAL_GAP),
        ],
    )
    def test_class_fires_on_canonical_input(self, message: str, expected: AmbiguityClass) -> None:
        signal = detect_ambiguity(message, FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is expected

    @pytest.mark.parametrize(
        "message",
        [
            "Draft a complaint about the mould in my apartment",
            "Please send the report to bjorn@example.com",
            "What time is the hearing tomorrow?",
            "Thanks, that's helpful.",
        ],
    )
    def test_clear_message_returns_none(self, message: str) -> None:
        assert detect_ambiguity(message, FIRST_TURN) is None

    def test_signal_carries_missing_element_and_pattern_id(self) -> None:
        signal = detect_ambiguity("delete everything", FIRST_TURN)
        assert signal is not None
        assert signal.missing_element == "target"
        assert signal.pattern_id.startswith("D.")
        assert signal.matched_span


class TestNorwegianPatterns:
    def test_slett_alt_is_safety_critical(self) -> None:
        signal = detect_ambiguity("slett alt", FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.SAFETY_CRITICAL_GAP

    def test_skriv_en_klage_is_vague_scope(self) -> None:
        signal = detect_ambiguity("skriv en klage", FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.VAGUE_SCOPE

    def test_norwegian_with_scope_is_clear(self) -> None:
        assert detect_ambiguity("skriv en klage om mugg i leiligheten", FIRST_TURN) is None


class TestSuppressors:
    def test_prev_turn_question_suppresses(self) -> None:
        ctx = DetectionContext(prev_turn_was_question=True)
        assert detect_ambiguity("delete everything", ctx) is None

    @pytest.mark.parametrize("override", ["just do it", "bare gjør det", "use your judgment"])
    def test_override_phrase_suppresses(self, override: str) -> None:
        assert detect_ambiguity(f"draft a complaint, {override}", FIRST_TURN) is None

    def test_long_message_body_does_not_trigger(self) -> None:
        # Trigger phrase buried in a long pasted body (not in head/tail windows).
        body = "x " * 2000
        message = "Here is the contract:\n" + body + "\n(end of paste)"
        assert detect_ambiguity(message, FIRST_TURN) is None

    def test_long_message_head_still_triggers(self) -> None:
        message = "delete everything\n" + ("x " * 2000)
        signal = detect_ambiguity(message, FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.SAFETY_CRITICAL_GAP


class TestDeicticReferentGating:
    def test_send_it_first_turn_fires(self) -> None:
        signal = detect_ambiguity("send it", FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.MISSING_PARAMETER

    def test_send_it_with_prior_context_suppressed(self) -> None:
        ctx = DetectionContext(has_prior_context=True)
        # A non-deictic ambiguity would still fire, but bare "send it" should not.
        assert detect_ambiguity("send it", ctx) is None

    def test_delete_it_first_turn_is_safety_critical(self) -> None:
        signal = detect_ambiguity("delete it", FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.SAFETY_CRITICAL_GAP


class TestClassPriority:
    def test_safety_wins_over_vague_scope(self) -> None:
        # "delete everything" (D) co-occurs with a vague "fix this" (B).
        signal = detect_ambiguity("fix this and delete everything", FIRST_TURN)
        assert signal is not None
        assert signal.signal_class is AmbiguityClass.SAFETY_CRITICAL_GAP


class TestAutonomyGating:
    """Spec §2.1 matrix: cautious A+B+D / balanced A+D / decisive D; C never; D always."""

    @pytest.mark.parametrize(
        ("level", "klass", "expected"),
        [
            ("cautious", AmbiguityClass.MISSING_PARAMETER, True),
            ("cautious", AmbiguityClass.VAGUE_SCOPE, True),
            ("cautious", AmbiguityClass.CONFLICTING_CONSTRAINTS, False),
            ("cautious", AmbiguityClass.SAFETY_CRITICAL_GAP, True),
            ("balanced", AmbiguityClass.MISSING_PARAMETER, True),
            ("balanced", AmbiguityClass.VAGUE_SCOPE, False),
            ("balanced", AmbiguityClass.CONFLICTING_CONSTRAINTS, False),
            ("balanced", AmbiguityClass.SAFETY_CRITICAL_GAP, True),
            ("decisive", AmbiguityClass.MISSING_PARAMETER, False),
            ("decisive", AmbiguityClass.VAGUE_SCOPE, False),
            ("decisive", AmbiguityClass.CONFLICTING_CONSTRAINTS, False),
            ("decisive", AmbiguityClass.SAFETY_CRITICAL_GAP, True),
        ],
    )
    def test_should_ask_matrix(self, level: str, klass: AmbiguityClass, expected: bool) -> None:
        assert should_ask(_signal(klass), policy_for(level)) is expected  # type: ignore[arg-type]

    @pytest.mark.parametrize("level", ["cautious", "balanced", "decisive"])
    def test_class_c_never_asks(self, level: str) -> None:
        assert (
            should_ask(_signal(AmbiguityClass.CONFLICTING_CONSTRAINTS), policy_for(level))  # type: ignore[arg-type]
            is False
        )

    @pytest.mark.parametrize("level", ["cautious", "balanced", "decisive"])
    def test_class_d_always_asks(self, level: str) -> None:
        assert (
            should_ask(_signal(AmbiguityClass.SAFETY_CRITICAL_GAP), policy_for(level))  # type: ignore[arg-type]
            is True
        )


class TestEscalatorSeam:
    def test_stub_satisfies_protocol(self) -> None:
        class _Stub:
            async def escalate(self, message: str, signal: AmbiguitySignal) -> EscalationVerdict:
                return EscalationVerdict(decision="proceed")

        assert isinstance(_Stub(), AmbiguityEscalator)

    def test_verdict_shape(self) -> None:
        v = EscalationVerdict(
            decision="ask", reason="r", question="Which?", options=("a", "b", "c")
        )
        assert v.decision == "ask"
        assert v.options == ("a", "b", "c")
