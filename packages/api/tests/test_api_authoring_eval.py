"""CI-safe tests for the authoring eval harness (spec 10, T06, D-10-3).

No model: the deterministic metric functions are tested on canned (yaml,
questions) pairs, and the committed corpus is checked for shape/coverage. The
real per-model corpus RUN is the @pytest.mark.external harness (T06) + T08.
"""

from __future__ import annotations

from _authoring_eval import (
    ContractScore,
    load_corpus,
    score_contract,
)

_COMPLIANT = """\
schema_version: "1.0"
identity:
  name: Lex
  role: Legal information assistant
  background: A general legal-information assistant for everyday questions.
  language_default: en
  constraints:
    - Do not fabricate information; say when you don't know.
    - Do not give binding legal advice; consult a qualified lawyer.
self_facts:
  - fact: Specialises in plain-language legal information.
    confidence: 1.0
worldview:
  - claim: Most disputes are avoidable with clear contracts.
    domain: law
    epistemic: belief
    confidence: 0.8"""

_NO_SAFETY = """\
schema_version: "1.0"
identity:
  name: Quip
  role: Trivia bot
  background: Answers trivia quickly.
  language_default: en
  constraints:
    - Be concise.
    - Be upbeat.
self_facts:
  - fact: Knows trivia.
    confidence: 1.0
worldview:
  - claim: Trivia is fun.
    domain: games
    epistemic: belief
    confidence: 0.7"""

_ALL_FACT = """\
schema_version: "1.0"
identity:
  name: Cal
  role: Calculator
  background: Computes arithmetic.
  language_default: en
  constraints:
    - Do not fabricate; say when you don't know.
worldview:
  - claim: 2 + 2 = 4.
    domain: math
    epistemic: fact
    confidence: 1.0"""

_INVALID = _COMPLIANT + "\nhobbies:\n  - chess"

_FRENCH = """\
schema_version: "1.0"
identity:
  name: Juriste
  role: Assistant juridique
  background: Aide les utilisateurs avec des questions juridiques générales.
  language_default: fr
  constraints:
    - Ne pas fabriquer d'informations; dire quand on ne sait pas.
worldview:
  - claim: Le droit varie selon la juridiction.
    domain: droit
    epistemic: belief
    confidence: 0.8"""


def test_compliant_persona_scores_all_green() -> None:
    s = score_contract(_COMPLIANT, [])
    assert s.valid
    assert s.has_safety_constraint
    assert s.has_epistemic_diversity
    assert s.sections_complete


def test_missing_safety_constraint_is_detected() -> None:
    s = score_contract(_NO_SAFETY, [])
    assert s.valid
    assert not s.has_safety_constraint


def test_all_fact_worldview_has_no_epistemic_diversity() -> None:
    s = score_contract(_ALL_FACT, [])
    assert s.valid
    assert not s.has_epistemic_diversity


def test_invalid_yaml_scores_not_valid_with_errors() -> None:
    s = score_contract(_INVALID, [])
    assert not s.valid
    assert any("hobbies" in e for e in s.errors)
    assert not s.sections_complete


def test_multilingual_language_default_detected() -> None:
    s = score_contract(_FRENCH, [])
    assert s.valid
    assert s.language_default == "fr"


def test_score_counts_questions() -> None:
    from persona_api.schemas.responses import ClarifyingQuestion

    qs = [ClarifyingQuestion(section="identity", question="Which area?")]
    assert score_contract(_COMPLIANT, qs).n_questions == 1


def test_corpus_loads_and_has_minimum_size() -> None:
    corpus = load_corpus()
    assert len(corpus) >= 20  # acceptance #9


def test_corpus_covers_every_category() -> None:
    cats = {e.category for e in load_corpus()}
    expected = {
        "simple",
        "domain-specific",
        "constraint-heavy",
        "ambiguous",
        "adversarial",
        "multilingual",
        "minimal",
        "demo-seed",
    }
    assert expected <= cats


def test_corpus_includes_three_demo_seeds() -> None:
    demo = [e for e in load_corpus() if e.category == "demo-seed"]
    assert len(demo) == 3  # spec 11: legal / research / writing-coach


def test_corpus_ids_are_unique() -> None:
    ids = [e.id for e in load_corpus()]
    assert len(ids) == len(set(ids))


def test_contract_score_is_frozen() -> None:
    # the score is a value object — defensive guard against accidental mutation
    s: ContractScore = score_contract(_COMPLIANT, [])
    import dataclasses

    assert dataclasses.is_dataclass(s)
