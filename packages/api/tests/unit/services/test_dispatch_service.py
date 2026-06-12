"""Unit tests for the auto-dispatch decision core — spec 21 T10.

The pure decision table (D-21-7/13/16/17), the consent question (D-21-16), the
answer parser, and the detection bridge are tested here without a database. The
``auto_dispatch`` side-effecting orchestration (run creation) is integration.
"""

from __future__ import annotations

import pytest
from persona.schema.persona import Persona, PersonaIdentity
from persona_api.services.dispatch_service import (
    consent_question,
    decide,
    detect_task,
    parse_consent_answer,
)
from persona_runtime.task_detector import TaskDetection


def _detection(*, dispatchable: bool = True) -> TaskDetection:
    return TaskDetection(
        capability="document_drafting",
        kind="skill",
        score=2.0,
        matched_phrases=("draft a",),
        dispatchable=dispatchable,
    )


class TestDecide:
    def test_no_detection_is_none(self) -> None:
        assert decide(None, None) == "none"
        assert decide(None, True) == "none"

    def test_ambiguous_detection_clarifies(self) -> None:
        assert decide(_detection(dispatchable=False), True) == "clarify"

    def test_granted_dispatches(self) -> None:
        assert decide(_detection(), True) == "dispatch"

    def test_never_asked_prompts_consent(self) -> None:
        assert decide(_detection(), None) == "ask_consent"

    def test_declined_falls_through_to_chat(self) -> None:
        # D-21-17: declined is stable — no dispatch, no re-prompt.
        assert decide(_detection(), False) == "declined"


class TestConsentQuestion:
    def test_three_options_and_free_form(self) -> None:
        q = consent_question("draft a complaint about mould")
        assert len(q.options) == 3
        assert q.allow_free_form is True
        assert "draft a complaint about mould" in q.question

    def test_options_are_grant_decline_modify(self) -> None:
        labels = [o.label for o in consent_question("x").options]
        assert "Yes" in labels[0]
        assert "No" in labels[1]
        assert "adjust" in labels[2].lower()


class TestParseConsentAnswer:
    @pytest.mark.parametrize("answer", ["Yes, run tasks automatically", "yes", "OK", "sure"])
    def test_grant(self, answer: str) -> None:
        assert parse_consent_answer(answer) == "grant"

    @pytest.mark.parametrize("answer", ["No, don't run this", "no", "nope"])
    def test_decline(self, answer: str) -> None:
        assert parse_consent_answer(answer) == "decline"

    @pytest.mark.parametrize("answer", ["Let me adjust it first", "actually change the scope", ""])
    def test_modify_is_the_safe_default(self, answer: str) -> None:
        # Never grant on an ambiguous free-form answer.
        assert parse_consent_answer(answer) == "modify"


class TestDetectTaskBridge:
    def test_in_scope_request_detects(self) -> None:
        persona = Persona(
            persona_id="p",
            identity=PersonaIdentity(name="n", role="r", background="b"),
            skills=["document_drafting"],
        )
        det = detect_task(persona, "draft a complaint about the mould")
        assert det is not None
        assert det.capability == "document_drafting"

    def test_out_of_scope_request_is_none(self) -> None:
        # The persona does not declare document_drafting → not detected (graceful).
        persona = Persona(
            persona_id="p",
            identity=PersonaIdentity(name="n", role="r", background="b"),
            skills=["web_research"],
        )
        assert detect_task(persona, "draft a complaint about the mould") is None
