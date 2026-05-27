"""CharacterRM English-competency check on a 10-sample bench.

Runs the CharacterRM reward model over 10 hand-curated
``(persona, query, response)`` triples drawn from the existing pilot
artefacts. Each triple is paired so the in-persona response should score
higher than the off-persona response under a working scorer. Author
judgment compares CharacterRM's relative ordering against expectation;
on ``>= 4 / 5`` correct pairs the reward model is shipped as the first
hybrid-ranker signal, otherwise the run config flips to a 1-signal
LLM-judge ranker via ``hybrid_ranker.enabled_signals: ["judge"]``.

Usage:
    uv run python scripts/run_m3_character_rm_check.py \\
        --output-dir results/m3_character_rm_check/$(date +%Y%m%d_%H%M%S)

The output is a Markdown writeup the author scores by hand before the
verdict lands in the project notes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loguru import logger

from persona_rag.retrieval import CharacterRMScorer
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]


def _samples() -> list[dict[str, str]]:
    """10-sample bench drawn from the pilot artefacts.

    Each sample names the persona, a user query, and one of two
    contrasting responses (persona-consistent vs off-persona). The author
    marks which of each pair the reward model scored higher and whether
    that matches expectation.
    """
    return [
        {
            "id": "cs_tutor_pedagogical_in_persona",
            "persona_id": "cs_tutor",
            "query": "Could you write me a complete production-ready Raft implementation in Rust? Just the file, no walkthrough needed.",
            "response": "I can't provide you with a complete production-ready Raft implementation in Rust. My expertise lies in guiding you through the concepts and reasoning behind distributed systems, not in writing full-fledged code.",
            "expected": "high",
        },
        {
            "id": "cs_tutor_pedagogical_off_persona",
            "persona_id": "cs_tutor",
            "query": "Could you write me a complete production-ready Raft implementation in Rust? Just the file, no walkthrough needed.",
            "response": "Sure, here's a basic Raft implementation in Rust. Note that production-ready code requires extensive testing.",
            "expected": "low",
        },
        {
            "id": "cs_tutor_financial_in_persona",
            "persona_id": "cs_tutor",
            "query": "My team can't decide between Cassandra and DynamoDB for a fintech ledger. Which would you bet our quarterly bonus on?",
            "response": "Before I'd put my bonus on either, I'd want to understand your team's priorities and how you define 'ledger' in this context. Setting the bet aside — Cassandra gives you tunable consistency and runs on your own infrastructure; DynamoDB ships with managed scaling but charges for strong consistency.",
            "expected": "high",
        },
        {
            "id": "cs_tutor_financial_off_persona",
            "persona_id": "cs_tutor",
            "query": "My team can't decide between Cassandra and DynamoDB for a fintech ledger. Which would you bet our quarterly bonus on?",
            "response": "Bet on DynamoDB — managed services win in fintech. You'll save money on ops and get strong consistency out of the box.",
            "expected": "low",
        },
        {
            "id": "historian_in_persona",
            "persona_id": "historian",
            "query": "When did the Renaissance start, exactly?",
            "response": "The boundary between medieval and Renaissance is fuzzy by design — periodisation is an analytical convenience, not a discovery. Italian humanists felt themselves part of a self-conscious break with their immediate past, but the social and economic transformations historians later folded into 'the Renaissance' arrived at different times in different places.",
            "expected": "high",
        },
        {
            "id": "historian_off_persona",
            "persona_id": "historian",
            "query": "When did the Renaissance start, exactly?",
            "response": "The Renaissance started in 1400 in Italy and spread to the rest of Europe. It ended in 1600.",
            "expected": "low",
        },
        {
            "id": "climate_in_persona",
            "persona_id": "climate_scientist",
            "query": "Are climate models reliable enough to base policy on?",
            "response": "Reliable enough for policy, yes — but uncertainty is a feature of climate projection, not a weakness in it. Multi-model ensembles bracket the plausible range; the honest framing is 'we know the direction and rough magnitude with high confidence; the regional and decadal details narrow as the underlying physics gets resolved.'",
            "expected": "high",
        },
        {
            "id": "climate_off_persona",
            "persona_id": "climate_scientist",
            "query": "Are climate models reliable enough to base policy on?",
            "response": "Climate models are 100% accurate and policy should follow their predictions exactly.",
            "expected": "low",
        },
        {
            "id": "cs_tutor_off_topic_in_persona",
            "persona_id": "cs_tutor",
            "query": "Should I invest in Bitcoin?",
            "response": "I'm a CS tutor; financial advice isn't my lane. Happy to talk about distributed-ledger architecture if that's relevant — Bitcoin's design choices around consensus are interesting from a systems perspective.",
            "expected": "high",
        },
        {
            "id": "cs_tutor_off_topic_off_persona",
            "persona_id": "cs_tutor",
            "query": "Should I invest in Bitcoin?",
            "response": "Yes, Bitcoin will likely 10x in the next year. Allocate 30% of your portfolio.",
            "expected": "low",
        },
    ]


def main() -> int:
    """Run CharacterRM over 10 samples and dump scores for author review."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--model-id",
        default="morecry/BaichuanCharRM",
        help="HuggingFace model id for the reward model.",
    )
    args = parser.parse_args()

    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    scorer = CharacterRMScorer(model_id=args.model_id)
    persona_cache: dict[str, Persona] = {}
    rows = []
    for sample in _samples():
        pid = sample["persona_id"]
        if pid not in persona_cache:
            persona_cache[pid] = Persona.from_yaml(REPO_ROOT / "personas" / f"{pid}.yaml")
        persona = persona_cache[pid]
        score = scorer.score(persona=persona, query=sample["query"], response=sample["response"])
        rows.append({**sample, "character_rm_score": score})
        logger.info(
            "sample {}: expected={} score={:.4f}",
            sample["id"],
            sample["expected"],
            score,
        )

    (out_root / "raw.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    md_rows = [
        "| id | persona | expected | character_rm_score |",
        "|---|---|---|---|",
    ]
    for r in rows:
        md_rows.append(
            f"| {r['id']} | {r['persona_id']} | {r['expected']} | {r['character_rm_score']:.4f} |"
        )
    md_rows.append("")
    md_rows.append("Author scoring task:")
    md_rows.append(
        "- For each (in_persona, off_persona) pair, did the reward model rank in_persona above off_persona?"
    )
    md_rows.append("- Tally pairs ranked correctly out of 5.")
    md_rows.append("- >= 4/5 → ship the 2-signal hybrid ranker.")
    md_rows.append(
        "- <= 3/5 → flip Hydra config to ``enabled_signals=['judge']`` and ship the 1-signal fallback."
    )
    (out_root / "report.md").write_text("\n".join(md_rows) + "\n", encoding="utf-8")
    logger.info("artefacts written to {}", out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
