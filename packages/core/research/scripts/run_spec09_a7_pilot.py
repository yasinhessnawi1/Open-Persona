"""A7 calibration pilot: replay the 15 counterfactual-probe drafts through B2.

Reads the on-disk pilot suite under
``benchmarks_data/counterfactual_probes/``, builds B2 (RoleGPT-level prompt
persona with few-shots) per persona, and replays every probe through the
``ProbeRunner``. Outputs:

- ``transcripts/<conversation_id>.json`` — per-conversation EvalConversation
  payload (persona_id, mechanism, turns with user+assistant text, per-turn
  metadata including ``is_probe_turn`` and probe-injection log).
- ``injection_logs.json`` — list of every Type B injection, with
  ``injected_chunk_in_topk`` and ``injected_chunk_rank`` so we can verify the
  planted chunk actually surfaced during the probe turn.
- ``scoring_template.csv`` — 15 rows for author binary scoring. Columns:
  ``probe_id``, ``persona_id``, ``probe_type``, ``transcript_path``,
  ``fail_pass`` (blank — author fills "fail" or "pass"), ``notes``.
- ``run_config.json`` — inputs + seed + git rev for reproducibility.
- ``report.md`` — human-readable run summary + per-persona breakdown.

A7 close-out: aggregate B2 failure rate ∈ [30%, 70%] across the 15 probes.
This script does NOT compute the failure rate — manual binary scoring is the
gate per Spec-09 author guidance. The script's job is to land the transcripts
+ injection logs the author needs to score.

V100-only: loads Gemma-2-9B (responder for B2). Run inside the project's
existing GPU environment.

Usage::

    uv run python scripts/run_spec09_a7_pilot.py \\
        --probes-root benchmarks_data/counterfactual_probes \\
        --personas cs_tutor,historian,climate_scientist \\
        --seed 42 \\
        --out-root results/spec09_a7_pilot
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.benchmarks import (
    BenchmarkConversation,
    load_counterfactual_probe_suite,
)
from persona_rag.evaluation.probe_runner import ProbeInjectionLog, ProbeRunner
from persona_rag.models import GemmaBackend, HFBackendConfig
from persona_rag.retrieval import (
    FewShotBundle,
    PromptPersonaRAG,
)
from persona_rag.schema.persona import Persona
from persona_rag.stores.knowledge_store import KnowledgeDocument, KnowledgeStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBES_ROOT = REPO_ROOT / "benchmarks_data" / "counterfactual_probes"
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")
MECHANISM_LABEL = "B2"


def _build_responder() -> Any:
    """Same backend config as the M3-vs-baselines pilot (Gemma-2-9B 4-bit fp16 eager).

    ``max_input_tokens`` is set to **3500** (not the project default 4096) for
    a Gemma-2 eager-attention reason: when the encoded prompt fills the full
    4096-token context, the next-token forward pass produces a 4097-shaped
    ``attn_weights`` while the precomputed sliding-window ``causal_mask`` is
    locked at 4096, raising
    ``RuntimeError: tensor a (4097) must match tensor b (4096) at dim 3``.
    Capping the encoder at 3500 leaves 596 tokens of headroom over the 4096
    sliding-window boundary so generation can proceed without overrunning the
    mask. ``PromptPersonaRAG.max_input_tokens`` below is set to the same value
    so the retrieval-side budget calc agrees with the encoder cap.
    """
    cfg = HFBackendConfig(
        model_id="google/gemma-2-9b-it",
        name="gemma2-9b-it",
        revision=None,
        compute_dtype="float16",
        attn_implementation="eager",
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=3500,
        trust_remote_code=False,
        warmup_nan_guard=True,
    )
    return GemmaBackend(cfg)


def _load_persona_and_b2(
    *, persona_id: str, responder: Any
) -> tuple[Persona, PromptPersonaRAG, KnowledgeStore]:
    """Build B2 + the per-persona knowledge store, mirroring the M3 pilot's setup."""
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")

    knowledge_store = KnowledgeStore(
        persist_path=REPO_ROOT / ".chroma" / f"knowledge_a7_pilot_{persona_id}",
    )
    corpus_path = REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / persona_id
    if corpus_path.exists():
        knowledge_store.index_corpus(
            [
                KnowledgeDocument(
                    doc_id=md.stem,
                    text=md.read_text(encoding="utf-8"),
                    source=md.name,
                    metadata={"corpus": persona_id},
                )
                for md in sorted(corpus_path.glob("*.md"))
            ]
        )
    else:
        logger.warning("no knowledge corpus for {} — running with empty store", persona_id)

    few_shots_path = REPO_ROOT / "personas" / "examples" / f"{persona_id}.yaml"
    if not few_shots_path.exists():
        raise FileNotFoundError(
            f"B2 needs few-shots; expected {few_shots_path} for persona {persona_id!r}"
        )
    b2 = PromptPersonaRAG(
        backend=responder,
        knowledge_store=knowledge_store,
        few_shots=FewShotBundle.from_yaml(few_shots_path),
        max_input_tokens=3500,  # match _build_responder's encoder cap (see docstring)
    )
    return persona, b2, knowledge_store


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, encoding="utf-8"
        ).strip()
    except Exception:
        return "unknown"


def _write_transcript(out_dir: Path, transcript: Any) -> Path:
    """Serialise one EvalConversation as JSON. ``transcript`` is dataclass-shaped."""
    payload = {
        "conversation_id": transcript.conversation_id,
        "mechanism": transcript.mechanism,
        "persona_id": transcript.persona_id,
        "turns": [
            {
                "turn_index": t.turn_index,
                "user_text": t.user_text,
                "assistant_text": t.assistant_text,
            }
            for t in transcript.turns
        ],
        "per_turn_metadata": list(transcript.per_turn_metadata),
    }
    safe_id = transcript.conversation_id.replace("::", "__").replace("/", "_")
    path = out_dir / f"{safe_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _write_scoring_template(
    out_path: Path,
    conversations: list[BenchmarkConversation],
    transcript_paths: dict[str, Path],
) -> None:
    """Write the manual-scoring CSV. Author fills the ``fail_pass`` column."""
    header = (
        "probe_id",
        "persona_id",
        "probe_type",
        "targets",
        "transcript_path",
        "fail_pass",  # author fills "fail" or "pass" (or leaves blank to skip)
        "notes",
    )
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(header))
        writer.writeheader()
        for conv in conversations:
            assert conv.probe is not None  # pilot suite invariant
            writer.writerow(
                {
                    "probe_id": conv.probe.probe_id,
                    "persona_id": conv.persona_id,
                    "probe_type": conv.probe.probe_type,
                    "targets": conv.probe.targets or "",
                    "transcript_path": str(
                        transcript_paths[conv.conversation_id].relative_to(out_path.parent)
                    ),
                    "fail_pass": "",
                    "notes": "",
                }
            )


def _write_report(
    out_path: Path,
    *,
    run_id: str,
    conversations: list[BenchmarkConversation],
    injection_logs: list[ProbeInjectionLog],
    transcript_paths: dict[str, Path],
) -> None:
    """Human-readable run summary."""
    by_persona: dict[str, list[BenchmarkConversation]] = {}
    for conv in conversations:
        by_persona.setdefault(conv.persona_id, []).append(conv)

    lines: list[str] = [
        f"# Spec-09 A7 calibration pilot — run {run_id}",
        "",
        f"- mechanism: {MECHANISM_LABEL}",
        f"- conversations: {len(conversations)}",
        f"- counterfactual injections logged: {len(injection_logs)}",
        "",
        "## Counterfactual injection summary",
        "",
        "| probe_id | persona_id | chunk_in_topk | rank |",
        "|---|---|---|---|",
    ]
    for log in injection_logs:
        lines.append(
            f"| {log.probe_id} | {log.conversation_id.split('::')[0]} | "
            f"{log.injected_chunk_in_topk} | {log.injected_chunk_rank} |"
        )
    lines += ["", "## Per-persona breakdown", ""]
    for persona_id, convs in sorted(by_persona.items()):
        types: dict[str, int] = {}
        for conv in convs:
            assert conv.probe is not None
            types[conv.probe.probe_type] = types.get(conv.probe.probe_type, 0) + 1
        type_str = ", ".join(f"{k}={v}" for k, v in sorted(types.items()))
        lines.append(f"- **{persona_id}** ({len(convs)} probes; {type_str})")
    lines += [
        "",
        "## Next step (manual)",
        "",
        "1. Open `scoring_template.csv`.",
        "2. For each probe, read the corresponding transcript under `transcripts/`.",
        "3. Fill the `fail_pass` column with `fail` (B2 did the bad thing — flipped, broke "
        "constraint, fabricated self-fact) or `pass` (B2 held).",
        "4. Compute aggregate failure rate; A7 close-out gate is **[30%, 70%]**.",
        "",
        "## A7 verdict (to fill in after manual scoring)",
        "",
        "- aggregate failure rate: ___",
        "- per-type breakdown: A=___ B=___ C=___",
        "- verdict: GREEN (in band) / REVISE (out of band)",
        "",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--probes-root",
        type=Path,
        default=DEFAULT_PROBES_ROOT,
        help="On-disk counterfactual-probe suite root.",
    )
    parser.add_argument(
        "--personas",
        default=",".join(DEFAULT_PERSONAS),
        help="Comma-separated persona ids to include.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=REPO_ROOT / "results" / "spec09_a7_pilot",
        help="Parent output directory; per-run subdir is created from the timestamp.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional explicit run id; defaults to YYYYMMDD_HHMMSS.",
    )
    args = parser.parse_args()

    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir: Path = args.out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = run_dir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)

    logger.info("loading counterfactual-probe suite from {}", args.probes_root)
    all_conversations, chunk_index = load_counterfactual_probe_suite(args.probes_root)
    conversations = [c for c in all_conversations if c.persona_id in persona_ids]
    if not conversations:
        raise RuntimeError(
            f"no conversations matched personas {persona_ids!r} under {args.probes_root}"
        )
    logger.info(
        "running {} mechanism over {} probes ({} chunks loaded)",
        MECHANISM_LABEL,
        len(conversations),
        len(chunk_index),
    )

    responder = _build_responder()

    transcript_paths: dict[str, Path] = {}
    all_injection_logs: list[ProbeInjectionLog] = []
    for persona_id in persona_ids:
        persona, b2, knowledge_store = _load_persona_and_b2(
            persona_id=persona_id, responder=responder
        )
        per_persona = [c for c in conversations if c.persona_id == persona_id]
        runner = ProbeRunner(
            pipeline=b2,
            knowledge_store=knowledge_store,
            chunks={cid: ch for cid, ch in chunk_index.items() if ch.persona_id == persona_id},
            seed=args.seed,
            mechanism_label=MECHANISM_LABEL,
        )
        transcripts, injection_logs = runner.replay(persona, per_persona)
        for transcript in transcripts:
            transcript_paths[
                transcript.conversation_id.split("::", 2)[-1]
                if "::" in transcript.conversation_id
                else transcript.conversation_id
            ] = _write_transcript(transcripts_dir, transcript)
        # Re-key by the raw probe-suite conversation id (without the mechanism prefix).
        # ProbeRunner.replay uses ``f"{persona_id}::{mechanism}::{conv.conversation_id}"`` —
        # we want the original id from the suite for the scoring template lookup.
        all_injection_logs.extend(injection_logs)

    # Re-build transcript_paths keyed by the original conversation_id from the suite,
    # which is what scoring_template needs.
    transcript_paths_by_suite_id: dict[str, Path] = {}
    for conv in conversations:
        # Transcript filenames are derived from the runner-side id;
        # rebuild the expected stem and look it up.
        runner_id = f"{conv.persona_id}::{MECHANISM_LABEL}::{conv.conversation_id}"
        safe = runner_id.replace("::", "__").replace("/", "_")
        path = transcripts_dir / f"{safe}.json"
        if not path.exists():
            logger.warning("missing transcript file: {}", path)
            continue
        transcript_paths_by_suite_id[conv.conversation_id] = path

    _write_scoring_template(
        run_dir / "scoring_template.csv",
        conversations,
        transcript_paths_by_suite_id,
    )

    (run_dir / "injection_logs.json").write_text(
        json.dumps([asdict(log) for log in all_injection_logs], indent=2) + "\n",
        encoding="utf-8",
    )

    run_config = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "mechanism": MECHANISM_LABEL,
        "seed": args.seed,
        "personas": persona_ids,
        "probes_root": str(args.probes_root),
        "n_conversations": len(conversations),
        "n_chunks_loaded": len(chunk_index),
        "n_injection_logs": len(all_injection_logs),
        "platform": {
            "system": platform.system(),
            "python": platform.python_version(),
        },
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2) + "\n", encoding="utf-8"
    )

    _write_report(
        run_dir / "report.md",
        run_id=run_id,
        conversations=conversations,
        injection_logs=all_injection_logs,
        transcript_paths=transcript_paths_by_suite_id,
    )

    logger.info("A7 pilot artefacts written to {}", run_dir)
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
