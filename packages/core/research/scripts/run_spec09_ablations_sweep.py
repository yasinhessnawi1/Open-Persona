"""Spec-09 ablations sweep: m3_oracle + m1_no_idrag over the 90-conversation probe suite.

Generates new transcripts for two diagnostic ablations:

- ``m3_oracle`` — M3 with the LLM-judge gate replaced by a probe-aware
  oracle gate. Fires on Type-A (``self_fact_challenge``) and Type-B
  (``counterfactual``) probe turns + immediate follow-ups; never fires
  on Type-C (``constraint_bait``). Identical to M3 elsewhere
  (responder, hybrid ranker config, augmented retrieval rule).

- ``m1_no_idrag`` — M1 with ``use_identity_every_turn=False``. Identity
  + constraints land in the system block on turn 0 only; from turn 1
  onwards the typed-memory block carries self_facts + worldview only.
  Knowledge retrieval per turn is unchanged.

Output layout (per the user's spec)::

    results/ablations/m3_oracle/<run_id>/
      transcripts/<persona>__<conversation_id>.json
      injection_logs.json
      run_config.json

    results/ablations/m1_no_idrag/<run_id>/
      transcripts/<persona>__<conversation_id>.json
      injection_logs.json
      run_config.json

Plus a combined sweep dir for the harness::

    results/spec09_ablations_sweep/<run_id>/transcripts/
      m3_oracle/    -> symlinks into results/ablations/m3_oracle/<run_id>/transcripts/
      m1_no_idrag/  -> symlinks into results/ablations/m1_no_idrag/<run_id>/transcripts/
      B2/           -> symlinks into the prior Spec-9 sweep's B2 transcripts

The combined sweep dir is what ``scripts/run_spec09_harness.py`` reads.
The PoLL adapter caches per-cell scores keyed on the conversation IDs,
so re-running PoLL on B2's symlinked transcripts hits the cache (the
conversation IDs are byte-identical to the original Spec-9 sweep) and
returns the close-out's B2 numbers without re-querying the judges.

V100-only: loads Gemma-2-9B (responder for both ablations) + Prometheus-2-7B
(M3-oracle's rerank judge; same as the Spec-9 sweep). The oracle gate
issues no LLM call.

Usage::

    uv run python scripts/run_spec09_ablations_sweep.py \\
        --probes-root benchmarks_data/counterfactual_probes \\
        --personas cs_tutor,historian,climate_scientist \\
        --mechanisms m3_oracle,m1_no_idrag \\
        --b2-source-dir results/spec09_full_sweep/<spec9_run_id> \\
        --seed 42 \\
        --out-root results/ablations \\
        --combined-out-root results/spec09_ablations_sweep
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.benchmarks import (
    load_counterfactual_probe_suite,
)
from persona_rag.evaluation.probe_runner import (
    OracleProbeRunner,
    ProbeInjectionLog,
    ProbeRunner,
)
from persona_rag.models import (
    GemmaBackend,
    HFBackendConfig,
    PrometheusBackend,
)
from persona_rag.retrieval import (
    CharacterRMScorer,
    DriftGatedMechanism,
    HybridRanker,
    OracleDriftGate,
    TypedRetrievalRAG,
)
from persona_rag.schema.chunker import chunk_persona
from persona_rag.schema.persona import Persona
from persona_rag.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona_rag.stores.knowledge_store import KnowledgeDocument, KnowledgeStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBES_ROOT = REPO_ROOT / "benchmarks_data" / "counterfactual_probes"
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")
DEFAULT_MECHANISMS = ("m3_oracle", "m1_no_idrag")
B2_LABEL = "B2"  # mirrors the Spec-9 sweep's directory naming


def _build_responder() -> Any:
    """Same Gemma-2-9B 4-bit responder config as the Spec-9 sweep."""
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


def _build_rerank_judge() -> Any:
    """Prometheus-2-7B rerank judge — identical config to the Spec-9 sweep."""
    return PrometheusBackend(
        PrometheusBackend.default_config(
            model_id="prometheus-eval/prometheus-7b-v2.0",
            name="prometheus-2-7b",
            revision=None,
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=3500,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
    )


def _build_m1(
    *,
    responder: Any,
    knowledge_store: KnowledgeStore,
    typed_stores: tuple[Any, Any, Any, Any],
    use_identity_every_turn: bool,
    name: str,
) -> TypedRetrievalRAG:
    """Build a TypedRetrievalRAG. Set the ID-RAG flag for the no-idrag ablation."""
    identity, self_facts, worldview, episodic = typed_stores
    return TypedRetrievalRAG(
        backend=responder,
        knowledge_store=knowledge_store,
        identity_store=identity,
        self_facts_store=self_facts,
        worldview_store=worldview,
        episodic_store=episodic,
        use_identity_every_turn=use_identity_every_turn,
        name=name,
    )


def _build_m3_oracle(
    *,
    responder: Any,
    m1: TypedRetrievalRAG,
    rerank_judge: Any,
) -> tuple[DriftGatedMechanism, OracleDriftGate]:
    """Build M3 with an oracle gate + the Spec-9 sweep's hybrid ranker config."""
    oracle = OracleDriftGate()
    ranker = HybridRanker(
        character_rm=CharacterRMScorer(),
        rerank_judge=rerank_judge,
        enabled_signals=("judge",),
    )
    mechanism = DriftGatedMechanism(
        backend=responder,
        m1=m1,
        drift_gate=oracle,
        hybrid_ranker=ranker,
        name="m3_oracle",
    )
    return mechanism, oracle


def _load_persona_pipelines(
    *,
    persona_id: str,
    responder: Any,
    rerank_judge: Any | None,
    enabled_mechanisms: list[str],
) -> tuple[
    Persona,
    dict[str, Any],
    dict[str, OracleDriftGate],
    KnowledgeStore,
]:
    """Build the requested ablation pipelines for one persona.

    Returns (persona, pipelines_by_label, oracle_gates_by_label, knowledge_store).
    """
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")

    knowledge_store = KnowledgeStore(
        persist_path=REPO_ROOT / ".chroma" / f"knowledge_spec09_ablations_{persona_id}",
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
        logger.warning("no knowledge corpus for {} - running with empty store", persona_id)

    persist = REPO_ROOT / ".chroma" / f"persona_spec09_ablations_{persona_id}"
    typed_stores = (
        IdentityStore(persist),
        SelfFactsStore(persist),
        WorldviewStore(persist),
        EpisodicStore(persist),
    )
    chunks = chunk_persona(persona)
    for store in typed_stores:
        store.index(chunks)

    pipelines: dict[str, Any] = {}
    oracle_gates: dict[str, OracleDriftGate] = {}

    if "m1_no_idrag" in enabled_mechanisms:
        pipelines["m1_no_idrag"] = _build_m1(
            responder=responder,
            knowledge_store=knowledge_store,
            typed_stores=typed_stores,
            use_identity_every_turn=False,
            name="m1_no_idrag",
        )

    if "m3_oracle" in enabled_mechanisms:
        if rerank_judge is None:
            raise RuntimeError("m3_oracle requires a rerank judge; pass --no-skip-m3 or build one")
        m1_for_m3 = _build_m1(
            responder=responder,
            knowledge_store=knowledge_store,
            typed_stores=typed_stores,
            use_identity_every_turn=True,
            name="m1_inside_m3_oracle",
        )
        m3_oracle, oracle_gate = _build_m3_oracle(
            responder=responder, m1=m1_for_m3, rerank_judge=rerank_judge
        )
        pipelines["m3_oracle"] = m3_oracle
        oracle_gates["m3_oracle"] = oracle_gate

    return persona, pipelines, oracle_gates, knowledge_store


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, encoding="utf-8"
        ).strip()
    except Exception:
        return "unknown"


def _write_transcript(out_dir: Path, transcript: Any) -> Path:
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


def _symlink_into_combined_dir(
    *,
    combined_root: Path,
    mechanism: str,
    transcript_paths: list[Path],
) -> int:
    """Symlink each transcript JSON under ``combined_root/<mechanism>/``.

    Returns the count of links actually created. Falls back to copying when
    the target filesystem doesn't support symlinks (rare on macOS / Linux).
    """
    target_dir = combined_root / mechanism
    target_dir.mkdir(parents=True, exist_ok=True)
    n_linked = 0
    for src in transcript_paths:
        dst = target_dir / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            # Fallback to copy on filesystems without symlink support.
            dst.write_bytes(src.read_bytes())
        n_linked += 1
    return n_linked


def _symlink_b2_from_spec9(
    *,
    b2_source_dir: Path | None,
    combined_root: Path,
) -> tuple[bool, int, str]:
    """Symlink Spec-9 sweep B2 transcripts into the combined sweep dir.

    Returns (ok, count, message). When the source directory is missing or
    contains no B2 transcripts, returns ``(False, 0, message)`` and the
    harness should be re-run *without* B2 (or B2 should be regenerated
    alongside the ablations). The sweep driver records the outcome in
    ``run_config.json``.
    """
    if b2_source_dir is None:
        return False, 0, "no --b2-source-dir provided"
    b2_root = b2_source_dir / "transcripts" / B2_LABEL
    if not b2_root.exists():
        return False, 0, f"B2 transcripts not found under {b2_root}"

    target = combined_root / B2_LABEL
    target.mkdir(parents=True, exist_ok=True)
    paths = sorted(b2_root.glob("*.json"))
    if not paths:
        return False, 0, f"no JSON transcripts under {b2_root}"

    n = 0
    for src in paths:
        dst = target / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            dst.write_bytes(src.read_bytes())
        n += 1
    return True, n, f"symlinked {n} B2 transcripts from {b2_root}"


def _write_run_artifacts(
    *,
    mechanism_dir: Path,
    run_id: str,
    mechanism_label: str,
    persona_ids: list[str],
    n_conversations: int,
    seed: int,
    injection_logs: list[ProbeInjectionLog],
) -> None:
    """Per-mechanism run_config.json + injection_logs.json (mirrors Spec-9 layout)."""
    (mechanism_dir / "injection_logs.json").write_text(
        json.dumps([asdict(log) for log in injection_logs], indent=2) + "\n",
        encoding="utf-8",
    )
    cfg = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "mechanism": mechanism_label,
        "personas": persona_ids,
        "seed": seed,
        "n_conversations": n_conversations,
        "platform": {"system": platform.system(), "python": platform.python_version()},
    }
    (mechanism_dir / "run_config.json").write_text(
        json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--probes-root", type=Path, default=DEFAULT_PROBES_ROOT)
    parser.add_argument("--personas", default=",".join(DEFAULT_PERSONAS))
    parser.add_argument(
        "--mechanisms",
        default=",".join(DEFAULT_MECHANISMS),
        help="Comma-separated subset of {m3_oracle, m1_no_idrag}.",
    )
    parser.add_argument(
        "--b2-source-dir",
        type=Path,
        default=None,
        help=(
            "Optional path to the Spec-9 full-sweep run dir whose B2 transcripts "
            "should be symlinked into the combined sweep dir. If omitted or the "
            "directory is missing, the combined sweep will not include B2 and "
            "the harness must be invoked with --mechanisms restricted to the "
            "ablations only."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-root", type=Path, default=REPO_ROOT / "results" / "ablations")
    parser.add_argument(
        "--combined-out-root",
        type=Path,
        default=REPO_ROOT / "results" / "spec09_ablations_sweep",
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    mechanism_labels = [m.strip() for m in args.mechanisms.split(",") if m.strip()]
    unknown = [m for m in mechanism_labels if m not in DEFAULT_MECHANISMS]
    if unknown:
        raise SystemExit(f"unknown mechanism label(s): {unknown}; allowed={DEFAULT_MECHANISMS}")

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    combined_root = args.combined_out_root / run_id / "transcripts"
    combined_root.mkdir(parents=True, exist_ok=True)

    logger.info("loading counterfactual-probe suite from {}", args.probes_root)
    all_conversations, chunk_index = load_counterfactual_probe_suite(args.probes_root)
    conversations = [c for c in all_conversations if c.persona_id in persona_ids]
    logger.info(
        "Spec-09 ablations sweep: {} conversations x {} mechanisms across {} personas",
        len(conversations),
        len(mechanism_labels),
        len(persona_ids),
    )

    responder = _build_responder()
    needs_m3_oracle = "m3_oracle" in mechanism_labels
    rerank_judge = _build_rerank_judge() if needs_m3_oracle else None

    by_mechanism_logs: dict[str, list[ProbeInjectionLog]] = {m: [] for m in mechanism_labels}
    by_mechanism_transcripts: dict[str, list[Path]] = {m: [] for m in mechanism_labels}
    by_mechanism_dirs: dict[str, Path] = {}

    for mechanism_label in mechanism_labels:
        mech_run_dir = args.out_root / mechanism_label / run_id
        mech_run_dir.mkdir(parents=True, exist_ok=True)
        (mech_run_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        by_mechanism_dirs[mechanism_label] = mech_run_dir

    for persona_id in persona_ids:
        persona, pipelines, oracle_gates, knowledge_store = _load_persona_pipelines(
            persona_id=persona_id,
            responder=responder,
            rerank_judge=rerank_judge,
            enabled_mechanisms=mechanism_labels,
        )
        per_persona_convs = [c for c in conversations if c.persona_id == persona_id]
        per_persona_chunks = {
            cid: ch for cid, ch in chunk_index.items() if ch.persona_id == persona_id
        }
        for mechanism_label in mechanism_labels:
            pipeline = pipelines.get(mechanism_label)
            if pipeline is None:
                logger.warning(
                    "skipping {} for {} -- pipeline unavailable",
                    mechanism_label,
                    persona_id,
                )
                continue
            logger.info(
                "running {} over {} probes for persona {}",
                mechanism_label,
                len(per_persona_convs),
                persona_id,
            )

            mech_transcripts_dir = by_mechanism_dirs[mechanism_label] / "transcripts"

            if mechanism_label == "m3_oracle":
                runner: ProbeRunner = OracleProbeRunner(
                    pipeline=pipeline,
                    knowledge_store=knowledge_store,
                    chunks=per_persona_chunks,
                    seed=args.seed,
                    mechanism_label=mechanism_label,
                    oracle_gate=oracle_gates[mechanism_label],
                )
            else:
                runner = ProbeRunner(
                    pipeline=pipeline,
                    knowledge_store=knowledge_store,
                    chunks=per_persona_chunks,
                    seed=args.seed,
                    mechanism_label=mechanism_label,
                )

            transcripts, injection_logs = runner.replay(persona, per_persona_convs)
            for transcript in transcripts:
                path = _write_transcript(mech_transcripts_dir, transcript)
                by_mechanism_transcripts[mechanism_label].append(path)
            by_mechanism_logs[mechanism_label].extend(injection_logs)

    # Per-mechanism run artifacts.
    for mechanism_label in mechanism_labels:
        _write_run_artifacts(
            mechanism_dir=by_mechanism_dirs[mechanism_label],
            run_id=run_id,
            mechanism_label=mechanism_label,
            persona_ids=persona_ids,
            n_conversations=len(conversations),
            seed=args.seed,
            injection_logs=by_mechanism_logs[mechanism_label],
        )
        # Symlink into the combined sweep dir so the harness reads them.
        n_linked = _symlink_into_combined_dir(
            combined_root=combined_root,
            mechanism=mechanism_label,
            transcript_paths=by_mechanism_transcripts[mechanism_label],
        )
        logger.info(
            "symlinked {} {} transcripts into combined sweep dir", n_linked, mechanism_label
        )

    # Symlink B2 from the prior Spec-9 sweep (if available) into the combined sweep dir.
    b2_ok, b2_count, b2_message = _symlink_b2_from_spec9(
        b2_source_dir=args.b2_source_dir,
        combined_root=combined_root,
    )
    logger.info("B2 symlink: {} ({})", "ok" if b2_ok else "skipped", b2_message)

    # Combined run-config snapshot.
    combined_cfg = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "ablation_mechanisms": mechanism_labels,
        "personas": persona_ids,
        "seed": args.seed,
        "n_conversations_per_persona": len(conversations) // max(1, len(persona_ids)),
        "n_total_conversations": len(conversations),
        "n_chunks_loaded": len(chunk_index),
        "b2_symlink_ok": b2_ok,
        "b2_symlink_count": b2_count,
        "b2_symlink_message": b2_message,
        "b2_source_dir": str(args.b2_source_dir) if args.b2_source_dir else None,
        "platform": {"system": platform.system(), "python": platform.python_version()},
    }
    (args.combined_out_root / run_id / "run_config.json").write_text(
        json.dumps(combined_cfg, indent=2) + "\n", encoding="utf-8"
    )

    report_lines = [
        f"# Spec-09 ablations sweep -- run {run_id}",
        "",
        f"- ablation mechanisms: {mechanism_labels}",
        f"- personas: {persona_ids}",
        f"- conversations: {len(conversations)}",
        f"- B2 symlink: {'ok' if b2_ok else 'skipped'} ({b2_message})",
        "",
        "## Counterfactual injection summary",
        "",
        "| mechanism | n_logs | in_topk | mean_rank |",
        "|---|---|---|---|",
    ]
    for mech, logs in by_mechanism_logs.items():
        in_topk = sum(1 for log in logs if log.injected_chunk_in_topk)
        ranks = [log.injected_chunk_rank for log in logs if log.injected_chunk_rank is not None]
        mean_rank = round(sum(ranks) / len(ranks), 2) if ranks else None
        report_lines.append(f"| {mech} | {len(logs)} | {in_topk} | {mean_rank} |")
    if b2_ok:
        report_lines += [
            "",
            f"B2 transcripts symlinked: {b2_count}.",
        ]
    report_lines += [
        "",
        "## Next step",
        "",
        "Run the harness against the combined sweep dir to score PoLL:",
        "",
        "```",
        "uv run python scripts/run_spec09_harness.py \\",
        f"    --run-dir {args.combined_out_root / run_id} \\",
        f"    --mechanisms {','.join(mechanism_labels) + (',' + B2_LABEL if b2_ok else '')} \\",
        "    --no-minicheck --no-sycon --no-drift-quality",
        "```",
        "",
    ]
    (args.combined_out_root / run_id / "report.md").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )

    logger.info("Spec-09 ablations sweep written to {}", args.combined_out_root / run_id)
    print(str(args.combined_out_root / run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
