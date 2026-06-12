"""Proactive clarifying questions — the 3+1 question primitive (spec 21).

The persona asks clarifying questions in a single format across both chat and
agentic-loop contexts (spec 21 §2.1): **exactly three predefined options plus a
free-form answer**. This module owns the format, the per-conversation dedup
registry, and the server-side answer validator:

- :class:`QuestionOption` / :class:`ProactiveQuestion` — the frozen 3+1 shape
  (D-21-9). Options carry ``{label, description}`` (Anthropic ergonomics), the
  free-form slot is implicit (``allow_free_form``).
- :class:`QuestionRegistry` — per-conversation/run dedup keyed on the normalized
  question hash, with answered-value reuse (D-21-6): a repeated question is not
  re-asked; its prior answer is injected instead.
- :func:`validate_answer` — the boundary validator (D-21-9): an answer must be
  one of the option labels or an accepted free-form submission, else the API
  rejects it. Structured options without validation are decoration.

The shape mirrors the existing ``ask_user`` surface additively: the
``RunEvent.asking_user`` constructor (``agentic/events.py``) gains an optional
``options`` payload — absent options renders the pre-spec-21 free-text prompt
(back-compat), present options renders the 3+1 UI (spec 21 T12).
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from persona_runtime.errors import InvalidQuestionAnswerError

__all__ = [
    "PROACTIVE_QUESTION_OPTION_COUNT",
    "ProactiveQuestion",
    "QuestionOption",
    "QuestionRegistry",
    "normalize_question",
    "validate_answer",
]

#: The number of predefined options every proactive question carries. The
#: fourth answer is the implicit free-form slot (the "3+1" shape, D-21-9).
PROACTIVE_QUESTION_OPTION_COUNT: int = 3

_PUNCT_EDGES = re.compile(r"^[^\w]+|[^\w]+$")
_WHITESPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Normalise a question for dedup keying (D-21-6).

    Casefolds, collapses internal whitespace to single spaces, and strips
    leading/trailing punctuation so ``"Draft a complaint?"`` and
    ``"draft a complaint"`` map to the same key. Deterministic and free — the
    right v0.1 scope; embedding-similarity dedup is a later upgrade.

    Args:
        text: The raw question text.

    Returns:
        The normalised key string.
    """
    collapsed = _WHITESPACE.sub(" ", text).strip()
    stripped = _PUNCT_EDGES.sub("", collapsed)
    return stripped.casefold()


class QuestionOption(BaseModel):
    """One predefined answer option (Anthropic-style ``{label, description}``).

    Attributes:
        label: The short answer text the user selects and that is submitted
            verbatim as the answer. Non-empty.
        description: Optional one-line elaboration shown under the label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    description: str = ""


class ProactiveQuestion(BaseModel):
    """A clarifying question in the 3+1 format (D-21-9).

    Frozen + ``extra="forbid"``. The free-form slot is implicit: when
    :attr:`allow_free_form` is true the user may answer in their own words in
    addition to the three predefined options.

    Attributes:
        question: The question text. Non-empty.
        options: Exactly :data:`PROACTIVE_QUESTION_OPTION_COUNT` predefined
            options; a recommended option is conventionally listed first.
        allow_free_form: Whether a free-form answer is accepted (default true).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    options: tuple[QuestionOption, ...]
    allow_free_form: bool = True

    @field_validator("options", mode="after")
    @classmethod
    def _exactly_three_options(
        cls, value: tuple[QuestionOption, ...]
    ) -> tuple[QuestionOption, ...]:
        if len(value) != PROACTIVE_QUESTION_OPTION_COUNT:
            msg = (
                f"a proactive question requires exactly {PROACTIVE_QUESTION_OPTION_COUNT} "
                f"predefined options (plus the implicit free-form slot); got {len(value)}"
            )
            raise ValueError(msg)
        return value

    def option_payload(self) -> list[dict[str, str]]:
        """Return the JSON-safe option list for an SSE event payload (D-21-9)."""
        return [{"label": o.label, "description": o.description} for o in self.options]

    def option_labels(self) -> tuple[str, ...]:
        """Return the option labels (the legal predefined answers)."""
        return tuple(o.label for o in self.options)


class QuestionRegistry:
    """Per-conversation/run dedup of asked questions, with answer reuse (D-21-6).

    Keyed on ``sha256(normalize_question(text))`` so paraphrase-identical
    questions collide. A registry instance is scoped to one conversation or one
    run; it is created fresh per conversation/run by the loop and (in a future
    increment) persisted alongside conversation state so dedup survives
    restarts — the in-memory form here is the v0.1 scope.

    Not frozen (it accumulates state across a conversation); deliberately the
    one mutable object in the proactive-question surface.
    """

    def __init__(self) -> None:
        self._answers: dict[str, str | None] = {}

    @staticmethod
    def _key(question: str) -> str:
        return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()

    def seen(self, question: str) -> bool:
        """Return whether a normalised-equal question has already been asked."""
        return self._key(question) in self._answers

    def answer_for(self, question: str) -> str | None:
        """Return the recorded answer for a prior question, or ``None``.

        ``None`` means either not-yet-asked or asked-but-unanswered; use
        :meth:`seen` to distinguish.
        """
        return self._answers.get(self._key(question))

    def record(self, question: str, answer: str | None = None) -> None:
        """Record that ``question`` was asked, optionally with its ``answer``.

        Recording the same question again with a non-``None`` answer fills in a
        previously-unanswered entry; a later ``None`` never clobbers a stored
        answer (idempotent toward the answered state).
        """
        key = self._key(question)
        if answer is not None or key not in self._answers:
            self._answers[key] = answer

    def __len__(self) -> int:
        return len(self._answers)


def validate_answer(question: ProactiveQuestion, answer: str) -> str:
    """Validate and canonicalise an answer to ``question`` (D-21-9, boundary).

    An answer is accepted if it matches one of the predefined option labels
    (case-insensitive, returned in its canonical casing) or, when the question
    allows it, is a non-empty free-form submission (returned stripped).

    Args:
        question: The question being answered.
        answer: The submitted answer text.

    Returns:
        The canonical answer string to fold into context.

    Raises:
        InvalidQuestionAnswerError: The answer is neither an option label nor an
            acceptable free-form submission.
    """
    stripped = answer.strip()
    for option in question.options:
        if option.label.casefold() == stripped.casefold():
            return option.label
    if question.allow_free_form and stripped:
        return stripped
    raise InvalidQuestionAnswerError(
        "answer matches neither a predefined option nor a free-form submission",
        context={
            "answer": answer[:120],
            "options": ", ".join(question.option_labels()),
            "allow_free_form": str(question.allow_free_form),
        },
    )
