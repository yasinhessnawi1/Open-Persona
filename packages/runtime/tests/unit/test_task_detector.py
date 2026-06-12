"""Tests for ``persona_runtime.task_detector`` — spec 21 T08 (R-21-3 §5).

Covers positive dispatch per seed entry, the adversarial negative corpus (must
return ``None``), allow-set filtering, word-boundary correctness, sub-dispatch
semantics, guards, the ambiguity margin, purity, and provenance.
"""

from __future__ import annotations

import pytest
from persona.schema.persona import Persona, PersonaIdentity
from persona_runtime.task_detector import (
    SEED_TRIGGERS,
    TaskTriggerRegistry,
    TriggerEntry,
    default_registry,
)

ALL_SKILLS = ["web_research", "document_drafting"]
ALL_TOOLS = ["web_search", "web_fetch", "file_write", "file_read", "image_generation"]


def _persona(*, skills: list[str] | None = None, tools: list[str] | None = None) -> Persona:
    return Persona(
        persona_id="p",
        identity=PersonaIdentity(name="n", role="r", background="b"),
        skills=skills if skills is not None else ALL_SKILLS,
        tools=tools if tools is not None else ALL_TOOLS,
    )


def _registry(**kw: list[str]) -> TaskTriggerRegistry:
    return default_registry(_persona(**kw))


class TestPositiveDispatch:
    @pytest.mark.parametrize(
        ("message", "capability", "kind"),
        [
            ("research the housing market in Oslo", "web_research", "skill"),
            ("investigate the landlord's history", "web_research", "skill"),
            ("draft a complaint about the mould", "document_drafting", "skill"),
            ("write a report on the findings", "document_drafting", "skill"),
            ("compose an email to the board", "document_drafting", "skill"),
            ("search the web for tenancy law", "web_search", "tool"),
            ("save this to a file please", "file_write", "tool"),
            ("generate an image of a red house", "image_generation", "tool"),
            ("draw me a logo", "image_generation", "tool"),
        ],
    )
    def test_canonical_phrase_dispatches(self, message: str, capability: str, kind: str) -> None:
        det = _registry().detect(message)
        assert det is not None
        assert det.capability == capability
        assert det.kind == kind
        assert det.dispatchable is True


class TestAdversarialNegatives:
    @pytest.mark.parametrize(
        "message",
        [
            "can you draft emails?",
            "are you able to research things?",
            "what does web_search do?",
            "how would you write a report?",
            "I don't need you to research this",
            "draft a letter without asking me first",
            "never mind, forget the research",
            "that research paper was helpful",  # bare domain noun, no intent verb
            "thanks for your help",
        ],
    )
    def test_returns_none(self, message: str) -> None:
        assert _registry().detect(message) is None


class TestAllowSetFiltering:
    def test_undeclared_skill_not_detected(self) -> None:
        reg = _registry(skills=["web_research"])  # document_drafting NOT declared
        assert reg.detect("draft a complaint about mould") is None

    def test_declared_skill_still_detected(self) -> None:
        reg = _registry(skills=["web_research"])
        assert reg.detect("research the housing market") is not None

    def test_undeclared_tool_not_detected(self) -> None:
        reg = _registry(tools=["web_search"])  # image_generation NOT declared
        assert reg.detect("generate an image of a cat") is None


class TestWordBoundary:
    def test_googled_does_not_match_google(self) -> None:
        # "googled" must not trigger the "google" web_search entry.
        assert _registry().detect("I googled it yesterday and moved on") is None

    def test_case_insensitive(self) -> None:
        assert _registry().detect("RESEARCH the market") is not None


class TestSubDispatch:
    def test_sub_dispatch_phrase_alone_is_none(self) -> None:
        # "look up" is sub-dispatch (cannot trigger alone).
        assert _registry().detect("look up the weather") is None

    def test_sub_dispatch_plus_dispatch_grade_fires(self) -> None:
        det = _registry().detect("look up and search the web for tenancy precedents")
        assert det is not None
        assert det.capability == "web_search"


class TestGuards:
    def test_fetch_without_url_is_none(self) -> None:
        assert _registry().detect("summarize this article for me") is None

    def test_fetch_with_url_detects(self) -> None:
        det = _registry().detect("fetch this page https://example.com/article")
        assert det is not None
        assert det.capability == "web_fetch"

    def test_file_read_requires_filename(self) -> None:
        assert _registry().detect("read the file") is None
        det = _registry().detect("read the file notes.txt")
        assert det is not None
        assert det.capability == "file_read"


class TestAmbiguityMargin:
    def test_near_tie_is_not_dispatchable_with_runner_up(self) -> None:
        # "research" (web_research) and "draft a" (document_drafting) both fire at
        # the same weight → within the ambiguity margin → a clarifying question.
        det = _registry().detect("research and draft a report on the dispute")
        assert det is not None
        assert det.dispatchable is False
        assert det.runner_up is not None


class TestPurityAndProvenance:
    def test_same_input_same_output(self) -> None:
        reg = _registry()
        a = reg.detect("research the market")
        b = reg.detect("research the market")
        assert a == b

    def test_matched_phrases_recorded(self) -> None:
        det = _registry().detect("investigate the landlord")
        assert det is not None
        assert any("investigate" in p.lower() for p in det.matched_phrases)

    def test_registry_extends_without_code_change(self) -> None:
        # An extra entry (data-only) adds a capability trigger (open/closed).
        extra = TriggerEntry(
            capability="web_research",
            kind="skill",
            phrases=("dig into",),
            dispatch_grade=True,
            weight=2.0,
        )
        reg = TaskTriggerRegistry(
            (*SEED_TRIGGERS, extra), allowed_skills=ALL_SKILLS, allowed_tools=ALL_TOOLS
        )
        det = reg.detect("dig into the case history")
        assert det is not None
        assert det.capability == "web_research"
