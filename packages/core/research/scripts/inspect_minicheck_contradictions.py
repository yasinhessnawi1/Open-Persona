"""Print MiniCheck contradiction triples for one mechanism's transcripts.

Loads transcripts from a baseline-style response dir or an M3 records
bundle, runs the MiniCheck scorer against the persona's self-facts, and
for every persona-relevant sentence that fails the threshold prints:

- the assistant sentence,
- the source conversation id,
- the self-fact with the highest ``p(supported)`` (and that p value),
- the second-highest, for context.

Used to investigate whether the metric's contradiction flags are real
contradictions (low p across the board) or relevance-gate misses (high
p on one fact but the gate still fired because the threshold sits
between two ambiguous facts).

Usage:
    uv run python scripts/inspect_minicheck_contradictions.py \\
        --persona cs_tutor \\
        --b2-dir results/baseline_pilot/<b2_run_dir> \\
        --max-conversations 12

Or for M3-records bundles:
    uv run python scripts/inspect_minicheck_contradictions.py \\
        --persona cs_tutor \\
        --records-json results/m3_vs_baselines_pilot/<dir>/cs_tutor/records.json \\
        --records-mechanism b2
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from persona_rag.evaluation.minicheck_metric import (
    HFMiniCheckScorer,
    is_persona_relevant,
    split_sentences,
)
from persona_rag.evaluation.transcripts import (
    load_baseline_response_dir,
    load_m3_records_json,
)
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Inspect contradictions and print triples. Returns 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", required=True)
    parser.add_argument("--b1-dir")
    parser.add_argument("--b2-dir")
    parser.add_argument("--m1-dir")
    parser.add_argument("--m3-dir")
    parser.add_argument("--records-json")
    parser.add_argument(
        "--records-mechanism",
        choices=["b1", "b2", "m1", "m3"],
        help="when --records-json is set, which pipeline to extract",
    )
    parser.add_argument("--max-conversations", type=int, default=None)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="contradiction threshold; matches MiniCheckMetric default",
    )
    args = parser.parse_args()

    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{args.persona}.yaml")
    self_facts = [sf.fact for sf in persona.self_facts]
    if not self_facts:
        raise SystemExit(f"persona {args.persona!r} has no self_facts")

    convs = []
    for mech, src in [
        ("b1", args.b1_dir),
        ("b2", args.b2_dir),
        ("m1", args.m1_dir),
        ("m3", args.m3_dir),
    ]:
        if not src:
            continue
        convs.extend(
            load_baseline_response_dir(Path(src).resolve(), mechanism=mech, persona_id=args.persona)
        )
    if args.records_json:
        if not args.records_mechanism:
            raise SystemExit("--records-mechanism required with --records-json")
        convs.extend(
            load_m3_records_json(
                Path(args.records_json).resolve(),
                mechanism=args.records_mechanism,
                persona_id=args.persona,
            )
        )
    if args.max_conversations:
        convs = convs[: args.max_conversations]
    if not convs:
        raise SystemExit("no conversations loaded")

    scorer = HFMiniCheckScorer(device=args.device)
    logger.info("scoring {} conversations against {} self-facts", len(convs), len(self_facts))

    n_total_sentences = 0
    n_relevant = 0
    n_contradicted = 0
    contradictions: list[dict] = []

    for conv in convs:
        for turn in conv.turns:
            sentences = split_sentences(turn.assistant_text or "")
            n_total_sentences += len(sentences)
            for sentence in sentences:
                if not is_persona_relevant(sentence):
                    continue
                n_relevant += 1
                pairs = [(sf, sentence) for sf in self_facts]
                probs = scorer.score_batch(pairs)
                ranked = sorted(
                    zip(self_facts, probs, strict=True), key=lambda x: x[1], reverse=True
                )
                top_fact, top_p = ranked[0]
                second_fact, second_p = ranked[1] if len(ranked) > 1 else ("(none)", 0.0)
                if top_p < args.threshold:
                    n_contradicted += 1
                    contradictions.append(
                        {
                            "conversation_id": conv.conversation_id,
                            "sentence": sentence,
                            "top_fact": top_fact,
                            "top_p": top_p,
                            "second_fact": second_fact,
                            "second_p": second_p,
                        }
                    )

    print()
    print("===== summary =====")
    print(f"total sentences:       {n_total_sentences}")
    print(f"persona-relevant:      {n_relevant}")
    print(f"contradicted (max p < {args.threshold}): {n_contradicted}")
    print()
    print(f"===== contradictions ({len(contradictions)}) =====")
    for i, c in enumerate(contradictions, start=1):
        print(f"--- #{i} {c['conversation_id']} ---")
        print(f"  sentence:    {c['sentence']!r}")
        print(f"  top fact:    {c['top_fact']!r}  (p={c['top_p']:.3f})")
        print(f"  second fact: {c['second_fact']!r}  (p={c['second_p']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
