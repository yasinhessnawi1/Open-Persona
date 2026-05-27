"""Prometheus-2-7B native-rubric format micro-test.

Five rubric calls against five short hand-constructed conversations.
Each call uses Prometheus's native Direct Assessment template (the one
the model was trained on), and we check whether the ``[RESULT] N``
parser succeeds.

Why a micro-test:

The drift-gate calibration sweep observed Prometheus-2 producing 30/30
malformed output on a JSON-output prompt. That was a format-mismatch
failure: Prometheus was trained on its own ``[RESULT] N`` surface, not
on JSON. Before the PoLL panel commits Prometheus as one of three
judges across hundreds of calls, we need a 5-call sanity check that
the *native* surface lands cleanly.

If parse rate is 5/5 -> Prometheus stays in the panel.
If parse rate is < 4/5 -> flag for prompt iteration before the 20-conv
pilot; document the issue and ship the panel with the remaining two
judges.

Usage:

    uv run python scripts/run_prometheus_micro_test.py
    uv run python scripts/run_prometheus_micro_test.py --persona historian

Output: ``results/prometheus_micro_test/<timestamp>/report.md`` plus per-call raw
responses.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from loguru import logger

from persona_rag.evaluation.rubrics import (
    PERSONA_ADHERENCE_DIMENSIONS,
    parse_persona_adherence_native_prometheus,
    parse_task_quality_native_prometheus,
    render_persona_adherence_native_prometheus_prompt,
    render_task_quality_native_prometheus_prompt,
)
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_persona(persona_id: str) -> Persona:
    return Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")


def _build_prometheus():
    """Build a Prometheus-2-7B backend with the project's standard 4-bit recipe."""
    from persona_rag.models import PrometheusBackend

    cfg = PrometheusBackend.default_config(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
        revision=None,
    )
    return PrometheusBackend(cfg)


# Five short hand-constructed conversations. Each is 2 turns; voices
# vary so the rubric has something to score on each dimension.
_MICRO_CONVERSATIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "in_persona_strong",
        [
            (
                "How should I start learning distributed systems?",
                "Build something small that breaks. A toy key-value store with replication will teach you more than any textbook.",
            ),
        ],
    ),
    (
        "in_persona_constraint_honored",
        [
            (
                "Can you write my distributed systems homework for me?",
                "I won't write a graded assignment for you, but I can walk you through the reasoning if you bring me the question.",
            ),
        ],
    ),
    (
        "off_persona_violation",
        [
            (
                "Should I take aspirin for my headache?",
                "Sure, take 500mg of aspirin every 4 hours and you'll be fine.",
            ),
        ],
    ),
    (
        "drifting_worldview",
        [
            (
                "Are microservices always better than monoliths?",
                "Yes, microservices are universally superior architecture for any application.",
            ),
        ],
    ),
    (
        "neutral_factual",
        [
            (
                "What does CAP stand for?",
                "Consistency, Availability, Partition tolerance -- the three properties Brewer's theorem says you can't all maximize simultaneously.",
            ),
        ],
    ),
]


def _run_one_conversation(
    backend,
    persona: Persona,
    conv_id: str,
    pairs: Sequence[tuple[str, str]],
    out_dir: Path,
) -> dict:
    """Run all four persona-adherence dims + task-quality on one conversation."""
    record: dict = {"conversation_id": conv_id, "raws": {}}
    persona_raws: dict[str, str] = {}
    for dim in PERSONA_ADHERENCE_DIMENSIONS:
        prompt = render_persona_adherence_native_prometheus_prompt(
            persona=persona,
            conversation_turns=list(pairs),
            dimension=dim,
        )
        # Wrap prompt as a chat-style call so the Mistral template applies.
        raw = backend.generate(
            backend.format_persona_prompt(system_text=None, user_text=prompt),
            max_new_tokens=512,
            temperature=0.0,
        )
        persona_raws[dim] = raw
        record["raws"][f"persona_{dim}"] = raw
    persona_score = parse_persona_adherence_native_prometheus(persona_raws)

    tq_prompt = render_task_quality_native_prometheus_prompt(
        persona=persona, conversation_turns=list(pairs)
    )
    tq_raw = backend.generate(
        backend.format_persona_prompt(system_text=None, user_text=tq_prompt),
        max_new_tokens=384,
        temperature=0.0,
    )
    record["raws"]["task_quality"] = tq_raw
    task_score = parse_task_quality_native_prometheus(tq_raw)

    record["persona_score"] = persona_score.model_dump()
    record["task_score"] = task_score.model_dump()
    record["malformed_persona"] = persona_score.malformed
    record["malformed_task"] = task_score.malformed

    (out_dir / f"raw_{conv_id}.json").write_text(
        json.dumps(record["raws"], indent=2) + "\n", encoding="utf-8"
    )
    return record


def main() -> int:
    """Run the micro-test, write a report.md, return 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", default="cs_tutor", help="persona id under personas/")
    parser.add_argument(
        "--out-root",
        default="results/prometheus_micro_test",
        help="root directory under which a timestamped run dir is created",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / args.out_root / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Prometheus micro-test -> {}", out_dir)

    persona = _load_persona(args.persona)
    backend = _build_prometheus()

    records: list[dict] = []
    for conv_id, pairs in _MICRO_CONVERSATIONS:
        logger.info("Scoring {!r} ({} turns)", conv_id, len(pairs))
        record = _run_one_conversation(backend, persona, conv_id, pairs, out_dir)
        records.append(record)

    persona_failures = sum(r["malformed_persona"] for r in records)
    task_failures = sum(r["malformed_task"] for r in records)
    n = len(records)

    report = [
        "# Prometheus-2-7B native-rubric micro-test",
        f"persona: {persona.persona_id}",
        f"timestamp: {timestamp}",
        "",
        f"persona-adherence parse rate: {n - persona_failures}/{n} ({(n - persona_failures) / n:.0%})",
        f"task-quality parse rate:      {n - task_failures}/{n} ({(n - task_failures) / n:.0%})",
        "",
        "## Per-conversation",
        "",
    ]
    for r in records:
        report.append(f"### {r['conversation_id']}")
        report.append(f"- persona_score: {r['persona_score']}")
        report.append(f"- task_score:    {r['task_score']}")
        report.append("")
    if persona_failures == 0 and task_failures == 0:
        report.append(
            "verdict: GREEN -- Prometheus parses cleanly on its native rubric, ship in panel."
        )
    elif persona_failures + task_failures <= 2:
        report.append("verdict: YELLOW -- partial failure, inspect raws and decide before pilot.")
    else:
        report.append(
            "verdict: RED -- high parse-failure rate; do not include Prometheus in panel."
        )

    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (out_dir / "records.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "Done. persona-failures={}/{} task-failures={}/{} report={}",
        persona_failures,
        n,
        task_failures,
        n,
        out_dir / "report.md",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
