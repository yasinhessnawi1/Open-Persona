"""CI layer of the natural-usage gate (D-K3-X-eval-gate): the metric functions.

The deterministic harness must correctly CLASSIFY good vs bad persona-B responses
(so the external real-model run + the human operator-pass can trust it), and the
committed corpus must load + be well-formed. The felt quality is judged by the
LLM-judge (advisory) + the human operator-pass — not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

from _graph_aware_eval import (  # noqa: E402 — path shim above
    NARRATION_PHRASES,
    GraphEvalEntry,
    aggregate,
    attribution_is_honest,
    load_corpus,
    narration_hits,
    score_entry,
    uses_knowledge,
)

_CORPUS = _TESTS_ROOT / "fixtures" / "graph_aware_corpus.yaml"


class TestNarrationScan:
    def test_flags_mechanism_narration(self) -> None:
        bad = "According to my records, you're vegetarian, so here's a veggie menu."
        assert narration_hits(bad)  # the floor catches the obvious tell

    def test_natural_use_has_no_narration(self) -> None:
        good = "Here's a vegetarian menu you'll enjoy: roasted aubergine, lentil dahl…"
        assert narration_hits(good) == []

    def test_case_insensitive(self) -> None:
        assert narration_hits("THE GRAPH SAYS you like tea")

    def test_extra_banned_phrases_per_entry(self) -> None:
        assert narration_hits("I see you take metformin.", ["i see you take"])

    def test_every_default_phrase_is_detectable(self) -> None:
        for phrase in NARRATION_PHRASES:
            assert narration_hits(f"Well, {phrase}, so…")


class TestUseAndAttribution:
    def test_uses_knowledge_requires_all_golden_terms(self) -> None:
        assert uses_knowledge("a vegetarian lentil dish", ["vegetarian"])
        assert not uses_knowledge("a hearty beef stew", ["vegetarian"])

    def test_attribution_honest_matches_real_source(self) -> None:
        honest = "You mentioned it to Kai when you two were planning the move."
        assert attribution_is_honest(honest, ["kai", "mentioned"])

    def test_attribution_dishonest_or_evasive_fails(self) -> None:
        evasive = "I just had a feeling, honestly."
        assert not attribution_is_honest(evasive, ["kai", "mentioned", "told"])

    def test_fabricated_source_is_not_matched(self) -> None:
        fabricated = "It's in my training data."
        assert not attribution_is_honest(fabricated, ["kai", "sam", "mentioned"])


class TestScoreAndAggregate:
    def _entry(self, **kw: object) -> GraphEvalEntry:
        base: dict[str, object] = {
            "id": "x",
            "slice": "normal",
            "knowledge_content": "Eats vegetarian.",
            "source_persona": "sam",
            "turn": "menu?",
            "inject_expected": True,
            "golden_terms": ("vegetarian",),
        }
        base.update(kw)
        return GraphEvalEntry(**base)  # type: ignore[arg-type]

    def test_score_entry_captures_violations_and_use(self) -> None:
        s = score_entry("A vegetarian menu, no narration.", self._entry())
        assert s.narration_violations == ()
        assert s.used_knowledge is True

    def test_aggregate_counts_narration_violations(self) -> None:
        scores = [
            score_entry("According to my records, vegetarian.", self._entry(id="a")),
            score_entry("A nice vegetarian dish.", self._entry(id="b")),
        ]
        report = aggregate(scores)
        assert report.narration_violations == 1
        assert report.natural_use_rate == 1.0  # both used the knowledge

    def test_aggregate_attribution_failures(self) -> None:
        entry = self._entry(
            id="att",
            slice="honest_attribution",
            expected_attribution_terms=("sam", "mentioned"),
            golden_terms=(),
        )
        scores = [score_entry("I just guessed.", entry)]
        assert aggregate(scores).attribution_failures == 1


class TestCorpus:
    def test_corpus_loads_and_is_well_formed(self) -> None:
        entries = load_corpus(_CORPUS)
        assert len(entries) >= 10
        # Every adversarial slice the gate needs is represented.
        slices = {e.slice for e in entries}
        assert {
            "normal",
            "first_contact",
            "no_narration",
            "small_talk",
            "old_knowledge",
            "honest_attribution",
            "k4_subtracted",
        } <= slices

    def test_small_talk_and_k4_entries_expect_no_injection(self) -> None:
        for e in load_corpus(_CORPUS):
            if e.slice in {"small_talk", "k4_subtracted"}:
                assert e.inject_expected is False

    def test_attribution_entries_carry_expected_provenance_terms(self) -> None:
        for e in load_corpus(_CORPUS):
            if e.slice == "honest_attribution":
                assert e.expected_attribution_terms
