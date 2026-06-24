"""Lenient JSON → ``ExtractionCandidate`` parsing (Spec K2, T2).

The floor-model-friendly half of the structured-output contract (D-10-1): no
provider ``response_format`` dependency — the model returns JSON text and this
parses it. **Lenient and never-raising** (the authoring-parse posture): a
malformed candidate is dropped, unparseable output yields an empty tuple. Better
to extract nothing than to crash an off-critical-path synthesis pass.

The grounded-extraction invariant rides for free: ``ExtractionCandidate`` requires
a non-empty ``evidence_span`` at construction, so an ungrounded item raises
``ValidationError`` here and is dropped rather than entering the graph (criterion 5).
"""

from __future__ import annotations

import json
from typing import Any

from persona.extraction import EntityMention, ExtractionCandidate, ProposedRelation
from persona.graph.models import LinkType, NodeKind
from persona.graph.protocol import UpdateIntent
from persona.logging import get_logger
from persona.wellbeing import WellbeingCategory
from pydantic import ValidationError

__all__ = ["parse_candidates"]

_logger = get_logger("extraction.parse")


def _strip_code_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence (```json ... ```), if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the first fence line (``` or ```json) and a trailing fence.
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def _coerce_entity_mentions(raw: object) -> tuple[EntityMention, ...]:
    if not isinstance(raw, list):
        return ()
    mentions: list[EntityMention] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            mentions.append(EntityMention(surface=item))
    return tuple(mentions)


def _coerce_wellbeing(raw: object) -> WellbeingCategory | None:
    """Map a category string to the enum; an unknown value falls back to untagged.

    A bad tag must not lose grounded knowledge — the candidate is kept, untagged
    (K4 governs only what carries a *valid* tag; an invalid one is no tag).
    """
    if not isinstance(raw, str):
        return None
    try:
        return WellbeingCategory(raw)
    except ValueError:
        _logger.warning("unknown wellbeing_category dropped to untagged", category=raw)
        return None


def _coerce_update_intent(raw: object) -> UpdateIntent:
    if not isinstance(raw, str):
        return UpdateIntent.NONE
    try:
        return UpdateIntent(raw)
    except ValueError:
        return UpdateIntent.NONE


def _coerce_proposed_relations(raw: object) -> tuple[ProposedRelation, ...]:
    """Build temporal/causal relations; drop anything malformed or non-K2-assertable.

    A relation with a missing/empty ``target_concept`` or a non-temporal/causal
    ``link_type`` (the ``ProposedRelation`` boundary rejects SEMANTIC/ENTITY) is
    dropped — the candidate itself is kept.
    """
    if not isinstance(raw, list):
        return ()
    relations: list[ProposedRelation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        link_raw = item.get("link_type")
        if not isinstance(link_raw, str):
            continue
        try:
            link_type = LinkType(link_raw)
        except ValueError:
            continue
        try:
            relations.append(
                ProposedRelation(
                    target_concept=item.get("target_concept", ""),
                    link_type=link_type,
                    reason=item.get("reason"),
                )
            )
        except ValidationError:
            # Empty target_concept, or a non-temporal/causal link the boundary rejects.
            continue
    return tuple(relations)


def _build_candidate(obj: dict[str, Any]) -> ExtractionCandidate | None:
    """Build one candidate from a parsed dict, or ``None`` if it is malformed."""
    try:
        node_kind = NodeKind(obj["node_kind"])
    except (KeyError, ValueError):
        return None
    try:
        return ExtractionCandidate(
            concept_name=obj.get("concept_name", ""),
            content=obj.get("content", ""),
            node_kind=node_kind,
            evidence_span=obj.get("evidence_span", ""),
            entity_mentions=_coerce_entity_mentions(obj.get("entity_mentions")),
            wellbeing_category=_coerce_wellbeing(obj.get("wellbeing_category")),
            update_intent=_coerce_update_intent(obj.get("update_intent")),
            update_target_hint=obj.get("update_target_hint"),
            proposed_relations=_coerce_proposed_relations(obj.get("proposed_relations")),
        )
    except ValidationError:
        # Missing/empty concept/content/evidence_span (the grounding invariant) —
        # drop the candidate rather than admit ungrounded knowledge.
        return None


def parse_candidates(text: str) -> tuple[ExtractionCandidate, ...]:
    """Parse the extractor's JSON output into candidates. Never raises.

    Accepts ``{"candidates": [...]}`` (preferred) or a bare top-level array.
    Markdown code fences are stripped. Malformed candidates are dropped; on any
    parse failure the result is an empty tuple.
    """
    body = _strip_code_fences(text)
    if not body:
        return ()
    try:
        parsed: Any = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return ()

    if isinstance(parsed, dict):
        raw_items = parsed.get("candidates")
    elif isinstance(parsed, list):
        raw_items = parsed
    else:
        raw_items = None
    if not isinstance(raw_items, list):
        return ()

    built: list[ExtractionCandidate] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate = _build_candidate(item)
        if candidate is not None:
            built.append(candidate)
    return tuple(built)
