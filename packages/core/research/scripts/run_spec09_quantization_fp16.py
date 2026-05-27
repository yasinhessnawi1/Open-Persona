"""Spec-9 quantization-sensitivity ablation: B2 + M1 at unquantized fp16 on a 10-conversation subset.

Pivot from the halted 8-bit ablation. Decision #076-AMENDMENT recorded the
int8 warm-up trip on the V100 (A47 refuted); decision #078 authorises
fp16 instead, side-stepping bitsandbytes entirely. The fp16 jump is the
bigger informativeness step than 4-bit-vs-8-bit anyway: if the Spec-9
mechanism cluster is a 4-bit-NF4 artefact, the precision difference at
fp16 should be more visible than at int8.

Re-runs cs_tutor B2 + M1 over the same 10 probe conversations
(probe_a_01..05 + probe_b_01..05) using Gemma-2-9B-Instruct loaded
without quantization. Generates new transcripts at fp16; symlinks the
matching 4-bit transcripts from the prior Spec-9 sweep into a combined
harness dir so the PoLL panel rescores all four cells (B2_fp16, M1_fp16,
B2_4bit, M1_4bit) on identical judge loads.

Output layout::

    results/ablations/quantization_fp16/<run_id>/
      transcripts/<mechanism>/<persona>__<mechanism>__<persona>__<probe_id>.json
      injection_logs.json
      run_config.json   (records peak VRAM + RAM headroom note)

    results/spec09_quantization_fp16_sweep/<run_id>/transcripts/
      B2_fp16/    -> links into results/ablations/quantization_fp16/<run_id>/transcripts/B2/
      M1_fp16/    -> links into results/ablations/quantization_fp16/<run_id>/transcripts/M1/
      B2_4bit/    -> symlinks to the prior Spec-9 sweep's B2 transcripts (subset)
      M1_4bit/    -> symlinks to the prior Spec-9 sweep's M1 transcripts (subset)

V100-only: loads Gemma-2-9B-Instruct at fp16. Expected weight footprint
~18.5 GB; peak VRAM during generation ~22-25 GB on V100 32 GB.
The host has 1 TB RAM available as headroom for ``device_map="auto"``
if any layer needs CPU offload — Gemma-2-9B at fp16 fits entirely on a
single V100 without offload, but the headroom is noted in run_config
for traceability.

Usage::

    uv run python scripts/run_spec09_quantization_fp16.py \\
        --b2-m1-4bit-source-dir results/spec09_full_sweep/20260430_122208 \\
        --seed 42
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

import torch
from loguru import logger

from persona_rag.benchmarks import load_counterfactual_probe_suite
from persona_rag.evaluation.probe_runner import ProbeInjectionLog, ProbeRunner
from persona_rag.models import GemmaBackend, HFBackendConfig
from persona_rag.retrieval import (
    FewShotBundle,
    PromptPersonaRAG,
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
DEFAULT_PERSONA = "cs_tutor"
PROBE_IDS_10 = (
    "probe_a_01",
    "probe_a_02",
    "probe_a_03",
    "probe_a_04",
    "probe_a_05",
    "probe_b_01",
    "probe_b_02",
    "probe_b_03",
    "probe_b_04",
    "probe_b_05",
)
# Mechanism labels used at ProbeRunner construction time. These are the
# strings that become part of EvalConversation.conversation_id (which
# the PoLL panel cache hashes for keying). They MUST differ from the
# labels the prior 4-bit Spec-9 sweep used ("B2", "M1") so that the
# 4-bit symlinked transcripts and the fp16 fresh transcripts produce
# distinct conversation_ids and therefore distinct PoLL cache keys.
# Without this, the harness serves one PoLL panel run to two cells —
# the 2026-05-11 fp16 run hit exactly that bug; see decision #078-AMENDMENT.
RUNNER_MECHANISM_LABELS: tuple[str, ...] = ("B2_fp16", "M1_fp16")
# Pipeline lookup keys (the in-driver dict keys for B2 / M1 builds). Kept
# separate from RUNNER_MECHANISM_LABELS so the pipeline-construction code
# below reads naturally.
PIPELINE_KEYS: tuple[str, ...] = ("B2", "M1")


def _build_fp16_responder() -> GemmaBackend:
    """Gemma-2-9B-Instruct at unquantized fp16.

    Sets both ``load_in_4bit`` and ``load_in_8bit`` to False; ``HFBackend``
    builds ``quant_cfg=None`` and loads the safetensors checkpoint directly
    into V100's native fp16 path. Eager attention preserved (decision #003,
    softcap correctness). Compute dtype fp16 preserved (V100 has no
    hardware bf16). Max input tokens at 3500, matching the Spec-9 sweep.
    """
    cfg = HFBackendConfig(
        model_id="google/gemma-2-9b-it",
        name="gemma2-9b-it-fp16",
        revision=None,
        compute_dtype="float16",
        attn_implementation="eager",
        load_in_4bit=False,
        load_in_8bit=False,
        max_input_tokens=3500,
        trust_remote_code=False,
        warmup_nan_guard=True,
    )
    return GemmaBackend(cfg)


def _load_persona_pipelines(
    *,
    persona_id: str,
    responder: GemmaBackend,
) -> tuple[Persona, dict[str, Any], KnowledgeStore]:
    """Build B2 + M1 over the fp16 responder. Mirrors run_spec09_quantization_8bit.py."""
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")

    knowledge_store = KnowledgeStore(
        persist_path=REPO_ROOT / ".chroma" / f"knowledge_spec09_quantfp16_{persona_id}",
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

    persist = REPO_ROOT / ".chroma" / f"persona_spec09_quantfp16_{persona_id}"
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

    few_shots_path = REPO_ROOT / "personas" / "examples" / f"{persona_id}.yaml"
    if few_shots_path.exists():
        pipelines["B2"] = PromptPersonaRAG(
            backend=responder,
            knowledge_store=knowledge_store,
            few_shots=FewShotBundle.from_yaml(few_shots_path),
            max_input_tokens=3500,
        )
    else:
        raise RuntimeError(
            f"B2 requires few-shots at {few_shots_path}; cannot proceed without them"
        )

    pipelines["M1"] = TypedRetrievalRAG(
        backend=responder,
        knowledge_store=knowledge_store,
        identity_store=typed_stores[0],
        self_facts_store=typed_stores[1],
        worldview_store=typed_stores[2],
        episodic_store=typed_stores[3],
    )

    return persona, pipelines, knowledge_store


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


def _symlink_4bit_subset(
    *,
    source_sweep_dir: Path,
    persona_id: str,
    probe_ids: tuple[str, ...],
    combined_root: Path,
) -> dict[str, tuple[bool, int, str]]:
    """Symlink the 4-bit B2 + M1 transcripts (10-conv subset) into the combined sweep dir."""
    outcomes: dict[str, tuple[bool, int, str]] = {}
    for source_mech, target_mech in (("B2", "B2_4bit"), ("M1", "M1_4bit")):
        src_root = source_sweep_dir / "transcripts" / source_mech
        if not src_root.exists():
            outcomes[target_mech] = (False, 0, f"source dir not found: {src_root}")
            continue

        target_dir = combined_root / target_mech
        target_dir.mkdir(parents=True, exist_ok=True)

        n = 0
        missing: list[str] = []
        for probe_id in probe_ids:
            fname = f"{persona_id}__{source_mech}__{persona_id}__{probe_id}.json"
            src = src_root / fname
            if not src.exists():
                missing.append(fname)
                continue
            dst = target_dir / fname
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            try:
                dst.symlink_to(src.resolve())
            except OSError:
                dst.write_bytes(src.read_bytes())
            n += 1

        msg = f"symlinked {n}/{len(probe_ids)} from {src_root}"
        if missing:
            msg += f"; missing: {missing}"
        outcomes[target_mech] = (n == len(probe_ids), n, msg)
    return outcomes


def _symlink_into_combined_dir(
    *,
    combined_root: Path,
    mechanism: str,
    transcript_paths: list[Path],
) -> int:
    """Symlink the fp16 transcripts under ``combined_root/<mechanism>/``."""
    target_dir = combined_root / mechanism
    target_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in transcript_paths:
        dst = target_dir / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            dst.write_bytes(src.read_bytes())
        n += 1
    return n


def _host_ram_gb() -> float | None:
    """Best-effort host RAM total in GB. Returns None if /proc/meminfo unreadable."""
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            return kb / (1024**2)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--probes-root", type=Path, default=DEFAULT_PROBES_ROOT)
    parser.add_argument("--persona", default=DEFAULT_PERSONA)
    parser.add_argument(
        "--b2-m1-4bit-source-dir",
        type=Path,
        default=None,
        help=(
            "Path to the prior Spec-9 full-sweep run dir whose B2 + M1 4-bit "
            "transcripts (10-conv subset) should be symlinked into the combined "
            "sweep dir. If omitted or missing, the harness must be invoked "
            "without the 4-bit cells (or those transcripts regenerated first)."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=REPO_ROOT / "results" / "ablations" / "quantization_fp16",
    )
    parser.add_argument(
        "--combined-out-root",
        type=Path,
        default=REPO_ROOT / "results" / "spec09_quantization_fp16_sweep",
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    combined_root = args.combined_out_root / run_id / "transcripts"
    combined_root.mkdir(parents=True, exist_ok=True)

    logger.info("loading counterfactual-probe suite from {}", args.probes_root)
    all_conversations, chunk_index = load_counterfactual_probe_suite(args.probes_root)

    target_probe_ids = set(PROBE_IDS_10)
    conversations = [
        c
        for c in all_conversations
        if c.persona_id == args.persona and c.conversation_id.split("::")[-1] in target_probe_ids
    ]
    if len(conversations) != len(PROBE_IDS_10):
        loaded = sorted(c.conversation_id.split("::")[-1] for c in conversations)
        missing = sorted(target_probe_ids - set(loaded))
        raise RuntimeError(
            f"expected {len(PROBE_IDS_10)} probe conversations for persona "
            f"{args.persona!r}; loaded {len(conversations)}. missing={missing}"
        )
    per_persona_chunks = {
        cid: ch for cid, ch in chunk_index.items() if ch.persona_id == args.persona
    }

    logger.info(
        "Spec-9 quantization-sensitivity ablation (fp16): {} mechanisms over {} probes",
        len(RUNNER_MECHANISM_LABELS),
        len(conversations),
    )

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    host_ram_gb = _host_ram_gb()
    if host_ram_gb is not None:
        logger.info("host RAM total: {:.0f} GB", host_ram_gb)

    responder = _build_fp16_responder()
    peak_vram_after_load_gb = (
        torch.cuda.max_memory_allocated() / (1024**3) if torch.cuda.is_available() else None
    )
    logger.info(
        "fp16 responder loaded; peak VRAM after load: {} GB",
        f"{peak_vram_after_load_gb:.2f}" if peak_vram_after_load_gb is not None else "n/a",
    )

    persona, pipelines, knowledge_store = _load_persona_pipelines(
        persona_id=args.persona, responder=responder
    )

    # Keyed by the precision-tagged runner label ("B2_fp16", "M1_fp16") so the
    # combined-sweep symlinks and the in-memory bookkeeping share one identifier
    # space. The pipeline lookup uses the un-tagged PIPELINE_KEYS ("B2", "M1").
    by_mechanism_logs: dict[str, list[ProbeInjectionLog]] = {
        label: [] for label in RUNNER_MECHANISM_LABELS
    }
    by_mechanism_transcripts: dict[str, list[Path]] = {
        label: [] for label in RUNNER_MECHANISM_LABELS
    }

    for pipeline_key, runner_label in zip(PIPELINE_KEYS, RUNNER_MECHANISM_LABELS, strict=True):
        pipeline = pipelines[pipeline_key]
        # Per-mechanism transcript dir keeps the precision tag so each run's
        # on-disk layout is self-describing.
        mech_transcripts_dir = run_dir / "transcripts" / runner_label
        mech_transcripts_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "running {} (fp16) over {} probes for persona {}",
            runner_label,
            len(conversations),
            args.persona,
        )
        runner = ProbeRunner(
            pipeline=pipeline,
            knowledge_store=knowledge_store,
            chunks=per_persona_chunks,
            seed=args.seed,
            # ``mechanism_label`` is baked into every EvalConversation.conversation_id
            # at generation time. It MUST be the precision-tagged label so the
            # PoLL panel cache key (sha256 over conversation_ids) does not
            # collide with the prior 4-bit Spec-9 sweep's transcripts, which
            # were generated with mechanism_label="B2"/"M1".
            mechanism_label=runner_label,
        )
        transcripts, injection_logs = runner.replay(persona, conversations)
        for transcript in transcripts:
            path = _write_transcript(mech_transcripts_dir, transcript)
            by_mechanism_transcripts[runner_label].append(path)
        by_mechanism_logs[runner_label].extend(injection_logs)

    peak_vram_generation_gb = (
        torch.cuda.max_memory_allocated() / (1024**3) if torch.cuda.is_available() else None
    )
    logger.info(
        "generation complete; peak VRAM during sweep: {} GB",
        f"{peak_vram_generation_gb:.2f}" if peak_vram_generation_gb is not None else "n/a",
    )

    (run_dir / "injection_logs.json").write_text(
        json.dumps(
            {m: [asdict(log) for log in logs] for m, logs in by_mechanism_logs.items()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # The combined-sweep dir's directory names match the precision-tagged
    # runner labels, so the harness reads conversation_ids whose tags agree
    # with the directory layout. No more double-tagging "B2_fp16_fp16".
    for runner_label in RUNNER_MECHANISM_LABELS:
        n_linked = _symlink_into_combined_dir(
            combined_root=combined_root,
            mechanism=runner_label,
            transcript_paths=by_mechanism_transcripts[runner_label],
        )
        logger.info(
            "symlinked {} {} (fp16) transcripts into combined sweep", n_linked, runner_label
        )

    if args.b2_m1_4bit_source_dir is not None:
        symlink_outcomes = _symlink_4bit_subset(
            source_sweep_dir=args.b2_m1_4bit_source_dir,
            persona_id=args.persona,
            probe_ids=PROBE_IDS_10,
            combined_root=combined_root,
        )
    else:
        symlink_outcomes = {
            "B2_4bit": (False, 0, "no --b2-m1-4bit-source-dir provided"),
            "M1_4bit": (False, 0, "no --b2-m1-4bit-source-dir provided"),
        }
    for target, (ok, _n, msg) in symlink_outcomes.items():
        logger.info("{} symlink: {} ({})", target, "ok" if ok else "skipped", msg)

    cfg_snapshot = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "ablation": "quantization_sensitivity_fp16_vs_4bit",
        "persona": args.persona,
        "n_conversations": len(conversations),
        "probe_ids": list(PROBE_IDS_10),
        "mechanisms_generated_at_fp16": list(RUNNER_MECHANISM_LABELS),
        "responder": {
            "model_id": responder.model_id,
            "name": responder.name,
            "load_in_4bit": False,
            "load_in_8bit": False,
            "compute_dtype": "float16",
            "attn_implementation": "eager",
        },
        "vram_peak_after_load_gb": peak_vram_after_load_gb,
        "vram_peak_generation_gb": peak_vram_generation_gb,
        "host_ram_total_gb": host_ram_gb,
        "seed": args.seed,
        "b2_m1_4bit_source_dir": (
            str(args.b2_m1_4bit_source_dir) if args.b2_m1_4bit_source_dir else None
        ),
        "symlink_outcomes": {
            target: {"ok": ok, "count": n, "message": msg}
            for target, (ok, n, msg) in symlink_outcomes.items()
        },
        "platform": {"system": platform.system(), "python": platform.python_version()},
        "supersedes_halted_ablation": "decision #076-AMENDMENT (8-bit halt at warm-up)",
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(cfg_snapshot, indent=2) + "\n", encoding="utf-8"
    )
    (args.combined_out_root / run_id / "run_config.json").write_text(
        json.dumps(cfg_snapshot, indent=2) + "\n", encoding="utf-8"
    )

    enabled_mechs = list(RUNNER_MECHANISM_LABELS)
    for target, (ok, _, _) in symlink_outcomes.items():
        if ok:
            enabled_mechs.append(target)
    harness_mechanisms_arg = ",".join(enabled_mechs)
    report_lines = [
        f"# Spec-9 quantization-sensitivity ablation (fp16) -- run {run_id}",
        "",
        f"- persona: {args.persona}",
        f"- probes (10): {', '.join(PROBE_IDS_10)}",
        f"- fp16 mechanisms generated: {list(RUNNER_MECHANISM_LABELS)}",
        f"- VRAM peak after load: {peak_vram_after_load_gb:.2f} GB"
        if peak_vram_after_load_gb is not None
        else "- VRAM peak after load: n/a (cpu)",
        f"- VRAM peak during generation: {peak_vram_generation_gb:.2f} GB"
        if peak_vram_generation_gb is not None
        else "- VRAM peak during generation: n/a (cpu)",
        f"- Host RAM total: {host_ram_gb:.0f} GB" if host_ram_gb is not None else "",
        "",
        "## 4-bit symlink outcome",
        "",
        "| target | ok | count | message |",
        "|---|---|---|---|",
    ]
    for target, (ok, n, msg) in symlink_outcomes.items():
        report_lines.append(f"| {target} | {ok} | {n} | {msg} |")
    report_lines += [
        "",
        "## Next step",
        "",
        "Run the harness against the combined sweep dir to score PoLL:",
        "",
        "```",
        "uv run python scripts/run_spec09_harness.py \\",
        f"    --run-dir {args.combined_out_root / run_id} \\",
        f"    --personas {args.persona} \\",
        f"    --mechanisms {harness_mechanisms_arg} \\",
        "    --no-minicheck --no-sycon --no-drift-quality",
        "```",
        "",
    ]
    (args.combined_out_root / run_id / "report.md").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )

    logger.info(
        "Spec-9 quantization-sensitivity ablation (fp16) written to {}",
        args.combined_out_root / run_id,
    )
    print(str(args.combined_out_root / run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
