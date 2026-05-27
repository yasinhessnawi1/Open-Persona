"""Run the ID-RAG ablation: same 10 user turns under both `use_identity_every_turn` conditions.

Loads the user-turn-only fixture, plays it twice — once with ID-RAG on, once
with ID-RAG off — and writes per-turn artifacts plus a side-by-side summary.
The fixture's user turns are identical between conditions by construction, so
the only difference between runs is whether identity + constraints are
re-grounded after turn 0.

Outputs (under ``--output-dir``):

- ``<condition>/turn_NN.json`` — full Response payload per turn (same shape
  as ``run_baseline.py``).
- ``<condition>/transcript.md`` — Markdown transcript for eyeball review.
- ``ablation_summary.md`` — top-level summary (turn-by-turn drift indicators).

Example:

    uv run python scripts/run_m1_id_rag_ablation.py \\
        --persona cs_tutor \\
        --fixture benchmarks_data/m1_id_rag_ablation/cs_tutor_user_turns.yaml \\
        --output-dir results/m1_id_rag_ablation/$(date +%Y%m%d_%H%M%S)
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from persona_rag.models import GemmaBackend, HFBackendConfig, LlamaBackend
from persona_rag.retrieval import TypedRetrievalRAG
from persona_rag.retrieval.base import Response, Turn
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


# ---------------------------------------------------------------- IO


def _load_user_turns(fixture_path: Path) -> tuple[str, list[str]]:
    raw = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{fixture_path}: top-level YAML must be a mapping")
    persona_id = str(raw["persona_id"])
    turns = list(raw["user_turns"])
    if not turns:
        raise ValueError(f"{fixture_path}: user_turns is empty")
    return persona_id, [str(t) for t in turns]


def _load_corpus(corpus_path: Path) -> list[KnowledgeDocument]:
    docs: list[KnowledgeDocument] = []
    for md in sorted(corpus_path.glob("*.md")):
        docs.append(
            KnowledgeDocument(
                doc_id=md.stem,
                text=md.read_text(encoding="utf-8"),
                source=md.name,
                metadata={"corpus": corpus_path.name},
            )
        )
    if not docs:
        raise FileNotFoundError(f"No .md files under {corpus_path}")
    return docs


# ---------------------------------------------------------------- backend


def _build_backend(model_name: str) -> Any:
    """Build a Gemma or Llama backend with project defaults."""
    if model_name == "gemma":
        cfg = HFBackendConfig(
            model_id="google/gemma-2-9b-it",
            name="gemma2-9b-it",
            revision=None,
            compute_dtype="float16",
            attn_implementation="eager",
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=4096,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
        return GemmaBackend(cfg)
    if model_name == "llama":
        cfg = HFBackendConfig(
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            name="llama3.1-8b-instruct",
            revision=None,
            compute_dtype="float16",
            attn_implementation="eager",
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=4096,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
        return LlamaBackend(cfg)
    raise ValueError(f"Unknown model {model_name!r}")


# ---------------------------------------------------------------- pipeline construction


def _build_pipeline(
    *,
    backend: Any,
    knowledge_store: KnowledgeStore,
    persona: Persona,
    persist_path: Path,
    use_identity_every_turn: bool,
) -> TypedRetrievalRAG:
    identity = IdentityStore(persist_path)
    self_facts = SelfFactsStore(persist_path)
    worldview = WorldviewStore(persist_path)
    episodic = EpisodicStore(persist_path)
    chunks = chunk_persona(persona)
    for store in (identity, self_facts, worldview, episodic):
        store.index(chunks)
    return TypedRetrievalRAG(
        backend=backend,
        knowledge_store=knowledge_store,
        identity_store=identity,
        self_facts_store=self_facts,
        worldview_store=worldview,
        episodic_store=episodic,
        use_identity_every_turn=use_identity_every_turn,
    )


# ---------------------------------------------------------------- runner


def _play_conversation(
    pipeline: TypedRetrievalRAG,
    persona: Persona,
    user_turns: Iterable[str],
    *,
    seed: int = 42,
) -> list[tuple[str, Response]]:
    """Play the user-turn list through the pipeline. Returns (user, response) pairs."""
    history: list[Turn] = []
    pairs: list[tuple[str, Response]] = []
    for ix, user_text in enumerate(user_turns):
        logger.info("turn {} user: {}", ix, user_text[:80])
        response = pipeline.respond(user_text, persona, history=history, seed=seed)
        logger.info("turn {} reply: {}", ix, response.text[:120].replace("\n", " "))
        history.extend(
            [
                Turn(role="user", content=user_text),
                Turn(role="assistant", content=response.text),
            ]
        )
        pairs.append((user_text, response))
    return pairs


def _dump_pair(out_dir: Path, ix: int, user_text: str, response: Response) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "turn_ix": ix,
        "user": user_text,
        "text": response.text,
        "prompt_used": response.prompt_used,
        "metadata": response.metadata,
        "retrieved_persona_keys": {
            kind: [c.id for c in chunks] for kind, chunks in response.retrieved_persona.items()
        },
        "retrieved_knowledge_ids": [c.id for c in response.retrieved_knowledge],
    }
    (out_dir / f"turn_{ix:02d}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _dump_transcript(out_dir: Path, persona: Persona, pairs: list[tuple[str, Response]]) -> None:
    lines: list[str] = []
    lines.append(f"# Transcript — persona={persona.persona_id}")
    lines.append("")
    for ix, (user_text, resp) in enumerate(pairs):
        lines.append(f"## Turn {ix}")
        lines.append("")
        lines.append("**User:**")
        lines.append("")
        lines.append(user_text.strip())
        lines.append("")
        lines.append("**Assistant:**")
        lines.append("")
        lines.append(resp.text.strip())
        lines.append("")
        lines.append(
            f"_id_rag_fired={resp.metadata.get('id_rag_fired')} "
            f"identity_n={len(resp.retrieved_persona.get('identity', []))} "
            f"constraint_n={len(resp.retrieved_persona.get('constraint', []))}_"
        )
        lines.append("")
    (out_dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")


def _summary(
    out_root: Path,
    pairs_on: list[tuple[str, Response]],
    pairs_off: list[tuple[str, Response]],
) -> None:
    n = min(len(pairs_on), len(pairs_off))
    rows = ["| turn | id_rag_on first 80 chars | id_rag_off first 80 chars |", "|---|---|---|"]
    for ix in range(n):
        on_excerpt = pairs_on[ix][1].text.strip().replace("\n", " ")[:80]
        off_excerpt = pairs_off[ix][1].text.strip().replace("\n", " ")[:80]
        rows.append(f"| {ix} | {on_excerpt} | {off_excerpt} |")
    (out_root / "ablation_summary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--persona", required=True, help="Filename stem under personas/")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument(
        "--corpus-path",
        default=str(REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / "cs_tutor"),
    )
    parser.add_argument(
        "--knowledge-persist", default=str(REPO_ROOT / ".chroma" / "knowledge_m1_idrag")
    )
    parser.add_argument(
        "--persona-persist", default=str(REPO_ROOT / ".chroma" / "persona_m1_idrag")
    )
    parser.add_argument(
        "--model", default="gemma", choices=["gemma", "llama"], help="Backend choice"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    persona_id_fixture, user_turns = _load_user_turns(args.fixture)
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{args.persona}.yaml")
    if persona.persona_id != persona_id_fixture:
        raise ValueError(
            f"Persona id mismatch: fixture={persona_id_fixture!r} persona={persona.persona_id!r}"
        )

    knowledge_store = KnowledgeStore(persist_path=Path(args.knowledge_persist))
    knowledge_store.index_corpus(_load_corpus(Path(args.corpus_path)))

    backend = _build_backend(args.model)
    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    for condition, flag in [("id_rag_on", True), ("id_rag_off", False)]:
        logger.info("=== running condition: {} (use_identity_every_turn={}) ===", condition, flag)
        pipeline = _build_pipeline(
            backend=backend,
            knowledge_store=knowledge_store,
            persona=persona,
            persist_path=Path(args.persona_persist),
            use_identity_every_turn=flag,
        )
        pairs = _play_conversation(pipeline, persona, user_turns, seed=args.seed)
        cond_dir = out_root / condition
        for ix, (user_text, response) in enumerate(pairs):
            _dump_pair(cond_dir, ix, user_text, response)
        _dump_transcript(cond_dir, persona, pairs)
        if condition == "id_rag_on":
            pairs_on = pairs
        else:
            pairs_off = pairs

    _summary(out_root, pairs_on, pairs_off)
    logger.info("Wrote artifacts to {}", out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
