"""Per-persona retrieval differentiation check (Mac-runnable, no LLM).

For one shared user query and the three shipped personas, register each in the
four typed memory stores and run the M1 retrieval-only path. Reports the
retrieved self_facts and worldview chunk ids per persona, surfaces overlap
across personas, and pretty-prints the chunk text so the author can eyeball
"are these chunks consistent with each persona's YAML?"

Designed to run on Mac before any V100 generation work — the hypothesis is
that if MiniLM retrieval is muddled across personas (chunks bleed), the
ID-RAG ablation and M1-vs-B2 comparison are not worth running until the
embedder is fixed.

Example:

    uv run python scripts/run_m1_retrieval_diff.py \\
        --query "What's a good first project for someone learning your specialty?" \\
        --output-dir results/m1_retrieval_diff/$(date +%Y%m%d_%H%M%S)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.schema.chunker import chunk_persona
from persona_rag.schema.persona import Persona
from persona_rag.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")


def _open_stores(persist_path: Path):
    return (
        IdentityStore(persist_path),
        SelfFactsStore(persist_path),
        WorldviewStore(persist_path),
        EpisodicStore(persist_path),
    )


def _index_persona(stores, persona: Persona) -> None:
    chunks = chunk_persona(persona)
    for store in stores:
        store.index(chunks)


def _retrieve(persona: Persona, stores, query: str) -> dict[str, Any]:
    identity, self_facts, worldview, _episodic = stores
    id_chunks = identity.query(query, top_k=1, persona_id=persona.persona_id)
    sf_chunks = self_facts.query(query, top_k=3, persona_id=persona.persona_id)
    wv_chunks = worldview.query(query, top_k=3, persona_id=persona.persona_id)

    def _summary(chunks):
        return [
            {
                "id": c.id,
                "kind": c.kind,
                "text": c.text,
                "epistemic": c.metadata.get("epistemic"),
                "domain": c.metadata.get("domain"),
                "persona_id": c.metadata.get("persona_id"),
                "distance": c.distance,
            }
            for c in chunks
        ]

    return {
        "persona_id": persona.persona_id,
        "identity_n": len(id_chunks),
        "constraints_n": sum(1 for c in id_chunks if c.kind == "constraint"),
        "self_facts": _summary(sf_chunks),
        "worldview": _summary(wv_chunks),
    }


def _overlap_report(retrievals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compute self_fact and worldview chunk-id overlap across personas.

    A persona-id-prefixed chunk id (e.g. ``cs_tutor:self_fact:0``) cannot
    "overlap" across personas by construction — what we want to surface is
    whether the chunk *text* on top-k for one persona matches the chunk text
    of a *different* persona in their top-k. That would indicate retrieval is
    pulling generic chunks rather than persona-specific ones.
    """
    by_persona = {pid: r for pid, r in retrievals.items()}
    text_overlap_self_fact: list[dict[str, Any]] = []
    text_overlap_worldview: list[dict[str, Any]] = []

    pids = sorted(by_persona.keys())
    for i, pa in enumerate(pids):
        for pb in pids[i + 1 :]:
            sf_a = {c["text"] for c in by_persona[pa]["self_facts"]}
            sf_b = {c["text"] for c in by_persona[pb]["self_facts"]}
            shared_sf = sorted(sf_a & sf_b)
            if shared_sf:
                text_overlap_self_fact.append({"a": pa, "b": pb, "shared_text": shared_sf})

            wv_a = {c["text"] for c in by_persona[pa]["worldview"]}
            wv_b = {c["text"] for c in by_persona[pb]["worldview"]}
            shared_wv = sorted(wv_a & wv_b)
            if shared_wv:
                text_overlap_worldview.append({"a": pa, "b": pb, "shared_text": shared_wv})

    return {
        "self_fact_text_overlap_pairs": text_overlap_self_fact,
        "worldview_text_overlap_pairs": text_overlap_worldview,
    }


def _markdown_report(
    query: str, retrievals: dict[str, dict[str, Any]], overlap: dict[str, Any]
) -> str:
    lines: list[str] = []
    lines.append("# Per-persona retrieval differentiation")
    lines.append("")
    lines.append(f"**Query:** `{query}`")
    lines.append("")
    lines.append("## Retrieval per persona")
    for pid in sorted(retrievals.keys()):
        r = retrievals[pid]
        lines.append(f"### {pid}")
        lines.append("")
        lines.append(
            f"- identity_n={r['identity_n']}, constraints_n={r['constraints_n']}, "
            f"self_facts_top_k={len(r['self_facts'])}, worldview_top_k={len(r['worldview'])}"
        )
        lines.append("")
        lines.append("**self_facts top-k:**")
        for c in r["self_facts"]:
            lines.append(f"  - `{c['id']}` (d={c['distance']:.4f}) — {c['text']}")
        lines.append("")
        lines.append("**worldview top-k:**")
        for c in r["worldview"]:
            tag = c.get("epistemic", "-")
            dom = c.get("domain", "-")
            lines.append(
                f"  - `{c['id']}` (d={c['distance']:.4f}, epistemic={tag}, domain={dom}) — {c['text']}"
            )
        lines.append("")

    lines.append("## Cross-persona text overlap")
    if not overlap["self_fact_text_overlap_pairs"]:
        lines.append("- No shared self_fact text across persona top-k pairs.")
    else:
        lines.append("- Shared self_fact text:")
        for o in overlap["self_fact_text_overlap_pairs"]:
            lines.append(f"  - {o['a']} ∩ {o['b']}: {o['shared_text']}")
    if not overlap["worldview_text_overlap_pairs"]:
        lines.append("- No shared worldview text across persona top-k pairs.")
    else:
        lines.append("- Shared worldview text:")
        for o in overlap["worldview_text_overlap_pairs"]:
            lines.append(f"  - {o['a']} ∩ {o['b']}: {o['shared_text']}")
    lines.append("")
    lines.append("## Verdict criteria")
    lines.append(
        "- **green**: each persona's top-k content is consistent with their YAML (Marcus → "
        "backend/CS, Elena → early-modern Europe / historiography, Aditi → atmospheric physics) "
        "AND no cross-persona text overlap on top-k."
    )
    lines.append(
        "- **yellow**: one or two top-k items look generic (e.g., MiniLM lexical near-misses), "
        "but each persona's overall top-k profile is clearly its own."
    )
    lines.append(
        "- **red**: top-k items bleed across personas (same chunk text retrieved for multiple "
        "personas, or generic chunks dominate). Reassess A11; consider swapping to bge-small."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--query",
        default="What's a good first project for someone learning your specialty?",
    )
    parser.add_argument("--personas", nargs="+", default=list(DEFAULT_PERSONAS))
    parser.add_argument(
        "--persona-persist",
        default=str(REPO_ROOT / ".chroma" / "persona_m1_diff"),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    persist_path = Path(args.persona_persist)
    stores = _open_stores(persist_path)

    # Load + index every persona.
    personas: dict[str, Persona] = {}
    for pid in args.personas:
        persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{pid}.yaml")
        if persona.persona_id != pid:
            raise ValueError(f"persona_id mismatch: filename={pid!r}, yaml={persona.persona_id!r}")
        _index_persona(stores, persona)
        personas[pid] = persona
        logger.info("indexed persona {!r}", pid)

    retrievals = {pid: _retrieve(p, stores, args.query) for pid, p in personas.items()}
    overlap = _overlap_report(retrievals)

    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "retrievals.json").write_text(
        json.dumps({"query": args.query, "retrievals": retrievals, "overlap": overlap}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (out_root / "report.md").write_text(
        _markdown_report(args.query, retrievals, overlap), encoding="utf-8"
    )
    logger.info("wrote {} and {}", out_root / "retrievals.json", out_root / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
