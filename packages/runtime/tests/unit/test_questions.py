"""Tests for ``persona_runtime.questions`` — spec 21 T04 (3+1 question primitive).

Covers the exactly-3-options invariant (D-21-9), the per-conversation dedup
registry with answer reuse (D-21-6), the normaliser, and the boundary answer
validator (D-21-9).
"""

from __future__ import annotations

import pytest
from persona_runtime.errors import InvalidQuestionAnswerError
from persona_runtime.questions import (
    PROACTIVE_QUESTION_OPTION_COUNT,
    ProactiveQuestion,
    QuestionOption,
    QuestionRegistry,
    normalize_question,
    validate_answer,
)
from pydantic import ValidationError


def _opts(n: int = 3) -> list[QuestionOption]:
    return [QuestionOption(label=f"Option {i}", description=f"d{i}") for i in range(n)]


def _question(text: str = "What is the focus?") -> ProactiveQuestion:
    return ProactiveQuestion(question=text, options=tuple(_opts()))


class TestProactiveQuestionShape:
    def test_three_options_accepted(self) -> None:
        q = _question()
        assert len(q.options) == PROACTIVE_QUESTION_OPTION_COUNT
        assert q.allow_free_form is True

    @pytest.mark.parametrize("n", [0, 1, 2, 4, 5])
    def test_wrong_option_count_rejected(self, n: int) -> None:
        with pytest.raises(ValidationError, match="exactly 3"):
            ProactiveQuestion(question="q?", options=tuple(_opts(n)))

    def test_empty_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProactiveQuestion(question="", options=tuple(_opts()))

    def test_option_requires_non_empty_label(self) -> None:
        with pytest.raises(ValidationError):
            QuestionOption(label="")

    def test_frozen_and_extra_forbid(self) -> None:
        q = _question()
        with pytest.raises(ValidationError):
            q.question = "x"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ProactiveQuestion.model_validate(
                {"question": "q?", "options": [o.model_dump() for o in _opts()], "bogus": 1}
            )

    def test_option_payload_is_json_safe_dicts(self) -> None:
        q = _question()
        assert q.option_payload() == [
            {"label": "Option 0", "description": "d0"},
            {"label": "Option 1", "description": "d1"},
            {"label": "Option 2", "description": "d2"},
        ]

    def test_option_labels(self) -> None:
        assert _question().option_labels() == ("Option 0", "Option 1", "Option 2")


class TestNormalizeQuestion:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Draft a complaint?", "draft a complaint"),
            ("  Draft   a  complaint  ", "draft a complaint!!!"),
            ("WHO is the landlord?", "who is the landlord"),
        ],
    )
    def test_paraphrase_identical_questions_normalise_equal(self, a: str, b: str) -> None:
        assert normalize_question(a) == normalize_question(b)

    def test_distinct_questions_differ(self) -> None:
        assert normalize_question("draft a complaint") != normalize_question("draft a letter")


class TestQuestionRegistry:
    def test_unseen_question_not_seen(self) -> None:
        reg = QuestionRegistry()
        assert reg.seen("anything?") is False
        assert reg.answer_for("anything?") is None

    def test_record_marks_seen(self) -> None:
        reg = QuestionRegistry()
        reg.record("Which focus?")
        assert reg.seen("which focus") is True  # normalised match
        assert reg.answer_for("Which focus?") is None
        assert len(reg) == 1

    def test_answer_reuse(self) -> None:
        reg = QuestionRegistry()
        reg.record("Which focus?")
        reg.record("which focus?", answer="Maintenance")
        assert reg.answer_for("WHICH FOCUS") == "Maintenance"

    def test_none_does_not_clobber_stored_answer(self) -> None:
        reg = QuestionRegistry()
        reg.record("q?", answer="A")
        reg.record("q?")  # re-ask with no new answer
        assert reg.answer_for("q?") == "A"

    def test_dedup_is_normalised(self) -> None:
        reg = QuestionRegistry()
        reg.record("Draft a complaint?")
        assert reg.seen("  draft   a complaint  ") is True


class TestValidateAnswer:
    def test_exact_option_label_accepted_and_canonicalised(self) -> None:
        q = _question()
        assert validate_answer(q, "option 1") == "Option 1"  # canonical casing

    def test_free_form_accepted_when_allowed(self) -> None:
        q = _question()
        assert validate_answer(q, "  something else  ") == "something else"

    def test_free_form_rejected_when_disallowed(self) -> None:
        q = ProactiveQuestion(question="q?", options=tuple(_opts()), allow_free_form=False)
        with pytest.raises(InvalidQuestionAnswerError, match="neither"):
            validate_answer(q, "not an option")

    def test_option_match_works_even_when_free_form_disallowed(self) -> None:
        q = ProactiveQuestion(question="q?", options=tuple(_opts()), allow_free_form=False)
        assert validate_answer(q, "Option 2") == "Option 2"

    def test_empty_answer_rejected(self) -> None:
        q = _question()
        with pytest.raises(InvalidQuestionAnswerError):
            validate_answer(q, "   ")

    def test_error_context_lists_options(self) -> None:
        q = ProactiveQuestion(question="q?", options=tuple(_opts()), allow_free_form=False)
        with pytest.raises(InvalidQuestionAnswerError) as exc:
            validate_answer(q, "nope")
        assert "Option 0" in exc.value.context["options"]
