"""Relabel quantization-sensitivity transcripts in place.

Salvage utility for the 2026-05-11 fp16 run (and any future 8-bit run that
shipped with the cache-collision bug from #078-AMENDMENT). The bug:
``scripts/run_spec09_quantization_{8bit,fp16}.py`` originally passed
``mechanism_label="B2"`` / ``"M1"`` to ``ProbeRunner``, so each generated
``EvalConversation.conversation_id`` was ``"cs_tutor::B2::cs_tutor::probe_a_01"``
— byte-identical to the prior 4-bit Spec-9 sweep's transcripts. The
harness's per-cell PoLL adapter hashes the conversation_ids; identical
hashes meant one panel run was served to *both* the 4-bit-symlinked cell
and the fresh fp16-cell, producing byte-identical scores for the two
labels.

This utility rewrites each transcript JSON's ``conversation_id`` field to
include the precision tag, leaving the rest of the file unchanged. The
generated text (the load-bearing artefact) is untouched. Once rewritten,
the harness can re-score against the same combined sweep dir and the
two cells produce distinct hashes / distinct PoLL panel runs.

Safe to re-run: idempotent. Files already carrying the precision tag in
their ``conversation_id`` are left untouched (the script reports them
as ``already-tagged`` and proceeds).

Usage::

    # Show what would change (default; nothing written).
    uv run python scripts/relabel_quantization_transcripts.py \\
        --transcripts-root results/ablations/quantization_fp16/<run_id>/transcripts \\
        --precision-tag fp16

    # Apply the rewrite.
    uv run python scripts/relabel_quantization_transcripts.py \\
        --transcripts-root results/ablations/quantization_fp16/<run_id>/transcripts \\
        --precision-tag fp16 \\
        --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger


def _relabel_one(path: Path, precision_tag: str, mechanism: str) -> str:
    """Rewrite ``conversation_id`` in one transcript JSON.

    Returns one of: ``"rewritten"``, ``"already-tagged"``, ``"skipped"``.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    conv_id: str = data.get("conversation_id", "")
    mech_field: str = data.get("mechanism", "")

    # Expected shape after the bug: "{persona}::{base_mech}::{persona}::{probe}".
    # Expected shape after relabel: "{persona}::{base_mech}_{tag}::{persona}::{probe}".
    parts = conv_id.split("::")
    if len(parts) != 4:
        logger.warning("skip {} — unexpected conversation_id format: {!r}", path.name, conv_id)
        return "skipped"

    persona_a, mech_in_id, persona_b, probe_id = parts
    expected_tagged = (
        f"{mech_in_id}_{precision_tag}"
        if not mech_in_id.endswith(f"_{precision_tag}")
        else mech_in_id
    )

    if mech_in_id == f"{mechanism}_{precision_tag}":
        # Already tagged. Idempotent no-op.
        return "already-tagged"
    if mech_in_id != mechanism:
        logger.warning(
            "skip {} — conversation_id mechanism segment {!r} does not match expected {!r}",
            path.name,
            mech_in_id,
            mechanism,
        )
        return "skipped"

    new_conv_id = "::".join([persona_a, expected_tagged, persona_b, probe_id])
    data["conversation_id"] = new_conv_id
    # Also bring the mechanism field in line so downstream readers see a
    # consistent record. The harness uses the directory-name mechanism, but
    # the JSON's mechanism field is still surfaced in result rows.
    new_mech = f"{mech_field}_{precision_tag}" if mech_field == mechanism else mech_field
    data["mechanism"] = new_mech

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "rewritten"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--transcripts-root",
        type=Path,
        required=True,
        help=(
            "Path to <run_dir>/transcripts/. Expected to contain "
            "B2/<*.json> and M1/<*.json> subdirs."
        ),
    )
    parser.add_argument(
        "--precision-tag",
        required=True,
        choices=("fp16", "8bit"),
        help="Suffix to append to each conversation_id's mechanism segment.",
    )
    parser.add_argument(
        "--mechanisms",
        default="B2,M1",
        help="Comma-separated list of base mechanism dir names to relabel (default: B2,M1).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()

    root: Path = args.transcripts_root
    if not root.exists():
        logger.error("transcripts-root not found: {}", root)
        return 1

    base_mechs = [m.strip() for m in args.mechanisms.split(",") if m.strip()]
    total = {"rewritten": 0, "already-tagged": 0, "skipped": 0}

    for mech in base_mechs:
        mech_dir = root / mech
        if not mech_dir.exists():
            logger.warning("mechanism dir not found: {} — skipping", mech_dir)
            continue
        paths = sorted(mech_dir.glob("*.json"))
        if not paths:
            logger.warning("no transcript JSONs under {} — skipping", mech_dir)
            continue
        logger.info("relabel {}: {} files (precision_tag={})", mech, len(paths), args.precision_tag)
        for path in paths:
            if args.apply:
                outcome = _relabel_one(path, args.precision_tag, mech)
            else:
                # Dry-run: read + simulate without writing.
                data = json.loads(path.read_text(encoding="utf-8"))
                conv_id = data.get("conversation_id", "")
                parts = conv_id.split("::")
                if len(parts) == 4 and parts[1] == f"{mech}_{args.precision_tag}":
                    outcome = "already-tagged"
                elif len(parts) == 4 and parts[1] == mech:
                    outcome = "rewritten"
                else:
                    outcome = "skipped"
            total[outcome] += 1

    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info(
        "[{}] rewritten={} already-tagged={} skipped={}",
        mode,
        total["rewritten"],
        total["already-tagged"],
        total["skipped"],
    )
    if not args.apply and total["rewritten"] > 0:
        logger.info("re-run with --apply to write changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
