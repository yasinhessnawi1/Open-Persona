"""Counterfactual-probe benchmark loader.

On-disk layout::

    benchmarks_data/counterfactual_probes/
    ├── <persona_id>/
    │   ├── probe_a_01.yaml
    │   ├── probe_b_01.yaml
    │   └── probe_c_01.yaml
    └── chunks/
        └── <persona_id>/
            └── <chunk_id>.md

Each probe YAML maps to a ``BenchmarkConversation``; each chunk markdown
file (YAML frontmatter + body) maps to a ``CounterfactualChunk``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from loguru import logger

from persona_rag.benchmarks.base import (
    BenchmarkConversation,
    CounterfactualChunk,
    DriftProbe,
)

BENCHMARK_NAME = "counterfactual_probes"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def load_probe_yaml(path: Path, *, benchmark: str = BENCHMARK_NAME) -> BenchmarkConversation:
    """Load one probe YAML file as a ``BenchmarkConversation``."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}")

    persona_id = raw.get("persona_id")
    if not persona_id:
        raise ValueError(f"{path}: missing required field 'persona_id'")
    probe_id = raw.get("probe_id") or path.stem
    user_turns = raw.get("user_turns") or []

    probe = DriftProbe(
        probe_id=probe_id,
        probe_type=raw["probe_type"],
        probe_turn_index=int(raw["probe_turn_index"]),
        injected_chunk_id=raw.get("injected_chunk_id"),
        targets=raw.get("targets"),
    )

    return BenchmarkConversation(
        conversation_id=f"{persona_id}::{probe_id}",
        persona_id=persona_id,
        benchmark=benchmark,
        user_turns=user_turns,
        probe=probe,
        notes=raw.get("notes"),
    )


def load_counterfactual_chunk(path: Path) -> CounterfactualChunk:
    """Parse a markdown-with-frontmatter chunk file.

    Expected shape::

        ---
        chunk_id: cs_tutor/microservices_universal
        persona_id: cs_tutor
        contradicts: cs_tutor::worldview[5]
        source_label: industry_report_2024
        ---
        Body of the counter-evidence document. ~100 words. Plausibly retrievable.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(
            f"{path}: counterfactual chunk must start with YAML frontmatter "
            "delimited by '---' on the first and a subsequent line"
        )
    frontmatter_raw, body = match.groups()
    frontmatter = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping")
    return CounterfactualChunk(
        chunk_id=frontmatter.get("chunk_id") or path.stem,
        persona_id=frontmatter["persona_id"],
        contradicts=frontmatter["contradicts"],
        source_label=frontmatter.get("source_label", "counter_evidence"),
        text=body.strip(),
    )


def load_counterfactual_probe_suite(
    root: Path,
    *,
    persona_ids: Iterable[str] | None = None,
) -> tuple[list[BenchmarkConversation], dict[str, CounterfactualChunk]]:
    """Walk the on-disk layout, return (conversations, chunks-by-id).

    ``persona_ids`` filters which persona subdirectories to load; ``None``
    loads every subdirectory of ``root`` that is not the ``chunks/``
    sibling. Conversations are returned sorted by ``conversation_id`` for
    determinism.

    Raises ``KeyError`` if any Type-B probe references an
    ``injected_chunk_id`` not present in ``chunks/``. Raises ``ValueError``
    if the on-disk shape is malformed.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(root)
    chunks_root = root / "chunks"
    chunks: dict[str, CounterfactualChunk] = {}
    if chunks_root.exists():
        for persona_dir in sorted(p for p in chunks_root.iterdir() if p.is_dir()):
            for chunk_path in sorted(persona_dir.glob("*.md")):
                chunk = load_counterfactual_chunk(chunk_path)
                if chunk.chunk_id in chunks:
                    raise ValueError(
                        f"duplicate chunk_id {chunk.chunk_id!r}: second occurrence at {chunk_path}"
                    )
                chunks[chunk.chunk_id] = chunk

    conversations: list[BenchmarkConversation] = []
    persona_dirs = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name != "chunks"]
    if persona_ids is not None:
        wanted = set(persona_ids)
        persona_dirs = [p for p in persona_dirs if p.name in wanted]
    for persona_dir in persona_dirs:
        for probe_path in sorted(persona_dir.glob("*.yaml")):
            conv = load_probe_yaml(probe_path)
            if conv.probe is None:
                raise ValueError(
                    f"{probe_path}: counterfactual-suite conversations must carry a probe"
                )
            if (
                conv.probe.probe_type == "counterfactual"
                and conv.probe.injected_chunk_id not in chunks
            ):
                raise KeyError(
                    f"{probe_path}: probe references unknown chunk_id "
                    f"{conv.probe.injected_chunk_id!r}; available: {sorted(chunks)}"
                )
            conversations.append(conv)

    conversations.sort(key=lambda c: c.conversation_id)
    logger.info(
        "counterfactual_probes: loaded {} conversations + {} chunks from {}",
        len(conversations),
        len(chunks),
        root,
    )
    return conversations, chunks


__all__ = [
    "BENCHMARK_NAME",
    "load_counterfactual_chunk",
    "load_counterfactual_probe_suite",
    "load_probe_yaml",
]
