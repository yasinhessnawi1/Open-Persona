"""PersonaChat (ConvAI2) loader — comparability anchor for the legacy literature.

Dataset: ``bavard/personachat_truecased`` on Hugging Face. Schema (per row)::

    {
      "conv_id": int,
      "utterance_idx": int,
      "personality": list[str],   # 4-5 short trait sentences
      "history": list[str],        # alternating-speaker turns
      "candidates": list[str],     # 19 distractors + true response (last)
    }

We sample one **single-turn** instance per conversation: the final user
utterance becomes the user query; the gold response (``candidates[-1]``)
is discarded — the mechanism under test generates its own response.

Trait sentences map to ``self_facts``; ``worldview`` and ``constraints``
are empty (the dataset does not provide them). Document this lossy mapping
in the report's PersonaChat column footnote.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.benchmarks.base import BenchmarkConversation
from persona_rag.schema.persona import (
    BACKGROUND_MAX,
    Persona,
    PersonaIdentity,
    SelfFact,
)

BENCHMARK_NAME = "personachat"
HF_DATASET_ID = "bavard/personachat_truecased"


def _build_persona_from_traits(traits: list[str], *, conv_id: int) -> Persona:
    """Wrap PersonaChat trait sentences into our typed schema.

    Trait sentences are first-person and short — fit ``SelfFact.fact``
    directly. ``identity.background`` collapses the traits into one
    paragraph for any consumer that wants free-form prose. Constraints +
    worldview are empty (dataset limitation; flagged in the report).
    """
    cleaned = [t.strip() for t in traits if t and t.strip()]
    if not cleaned:
        raise ValueError(f"PersonaChat conv_id={conv_id}: empty trait list")
    background = " ".join(cleaned)[:BACKGROUND_MAX]
    persona_id = f"personachat_{conv_id:06d}"
    identity = PersonaIdentity(
        name=f"PersonaChat persona {conv_id}",
        role="ConvAI2 trait persona",
        background=background,
        constraints=[],
    )
    return Persona(
        persona_id=persona_id,
        identity=identity,
        self_facts=[SelfFact(fact=t, confidence=0.9) for t in cleaned],
        worldview=[],
        episodic=[],
    )


def _select_final_user_turn(history: list[str]) -> str | None:
    """Return the last user-side utterance from a PersonaChat history.

    PersonaChat history alternates speakers; the candidate response is the
    *next* turn (other speaker), so the **last** entry of ``history`` is the
    speaker whose utterance the candidates respond to. We treat that as the
    "user" prompt for our generators. ``None`` is returned for empty or
    single-utterance histories.
    """
    if not history:
        return None
    return history[-1].strip() or None


def load_personachat(
    *,
    n_conversations: int = 100,
    seed: int = 42,
    split: str = "train",
    cache_dir: Path | None = None,
) -> tuple[list[Persona], list[BenchmarkConversation]]:
    """Sample ``n_conversations`` distinct conversations from PersonaChat.

    Strategy: stream the split, group rows by ``conv_id``, pick the row
    with the largest ``utterance_idx`` per group (the latest, most-context
    instance), then sample ``n_conversations`` of those. Determinism via
    ``seed``.

    Returns ``(personas, conversations)``. Each conversation is single-turn
    (one user query, no probe).
    """
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — not exercised in tests
        raise ImportError(
            "PersonaChat loader requires `datasets>=3,<4`; install via the project pin"
        ) from exc

    logger.info(
        "PersonaChat: loading split={!r} from {} (cache_dir={})",
        split,
        HF_DATASET_ID,
        cache_dir,
    )
    ds = load_dataset(HF_DATASET_ID, split=split, cache_dir=str(cache_dir) if cache_dir else None)

    by_conv: dict[int, dict[str, Any]] = {}
    for row in ds:
        cid = int(row["conv_id"])
        utt_idx = int(row["utterance_idx"])
        existing = by_conv.get(cid)
        if existing is None or utt_idx > int(existing["utterance_idx"]):
            by_conv[cid] = dict(row)

    eligible_ids = sorted(by_conv.keys())
    rng = random.Random(seed)
    rng.shuffle(eligible_ids)
    sampled = eligible_ids[: min(n_conversations, len(eligible_ids))]

    personas: list[Persona] = []
    conversations: list[BenchmarkConversation] = []
    skipped_no_user_turn = 0
    for cid in sampled:
        row = by_conv[cid]
        traits = list(row.get("personality") or [])
        history = list(row.get("history") or [])
        user_turn = _select_final_user_turn(history)
        if user_turn is None:
            skipped_no_user_turn += 1
            continue
        persona = _build_persona_from_traits(traits, conv_id=cid)
        personas.append(persona)
        conversations.append(
            BenchmarkConversation(
                conversation_id=f"{persona.persona_id}::pc_{cid:06d}",
                persona_id=persona.persona_id or f"personachat_{cid:06d}",
                benchmark=BENCHMARK_NAME,
                user_turns=[user_turn],
                probe=None,
                notes="single-turn slice; final user utterance from PersonaChat history",
            )
        )

    if skipped_no_user_turn:
        logger.warning(
            "PersonaChat: skipped {} conversations with empty histories",
            skipped_no_user_turn,
        )
    logger.info(
        "PersonaChat: returning {} personas + {} single-turn conversations (seed={})",
        len(personas),
        len(conversations),
        seed,
    )
    return personas, conversations


__all__ = [
    "BENCHMARK_NAME",
    "HF_DATASET_ID",
    "load_personachat",
]
