"""Per-store write-policy table.

Each store class owns a small data-driven ``_POLICY`` table that
:func:`evaluate_write_policy` consults. Decisions are inspectable as plain
data rather than buried in conditional logic — easier to test, easier to
audit.

See ``docs/specs/spec_01/spec_01_core.md`` §5.2 for the two-axis policy
(source × force-flag) and D-01-5 for the persona-self confidence threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from persona.errors import PersonaSelfWriteForbiddenError, RuntimeWriteForbiddenError
from persona.schema.chunks import WriteSource

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk

__all__ = [
    "PersonaSelfRequirement",
    "PolicyDecision",
    "PolicyRule",
    "PolicyTable",
    "evaluate_write_policy",
]


class PolicyDecision(StrEnum):
    """The three outcomes of a per-source policy check.

    Values:
        ACCEPT: Write proceeds.
        REJECT: Write is forbidden regardless of ``force``.
        FORCE_ONLY: Write proceeds only when ``force=True`` AND any
            source-specific extra check (see :class:`PolicyRule`) passes.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    FORCE_ONLY = "force_only"


class PersonaSelfRequirement(StrEnum):
    """Extra checks applied to ``persona_self`` writes when policy is FORCE_ONLY.

    Values:
        NONE: No extra check — ``force=True`` alone is enough.
        CONFIDENCE_THRESHOLD: ``chunk.metadata['confidence'] >= threshold``
            (per D-01-5, threshold 0.8 for self_facts).
        EPISTEMIC_TAG_SET: ``chunk.metadata['epistemic']`` must be a non-empty
            value (worldview).
    """

    NONE = "none"
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    EPISTEMIC_TAG_SET = "epistemic_tag_set"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One row of the policy table.

    Attributes:
        decision: Outcome for the matched (source, force) combination.
        persona_self_requirement: For ``persona_self`` writes this gates
            the extra check applied on top of ``force=True``.
        persona_self_confidence_threshold: Threshold used when
            ``persona_self_requirement == CONFIDENCE_THRESHOLD``.
        requires_reason: When True, persona_self writes also need a
            non-empty ``reason``. Always-True for persona_self per spec §5.2.
    """

    decision: PolicyDecision
    persona_self_requirement: PersonaSelfRequirement = PersonaSelfRequirement.NONE
    persona_self_confidence_threshold: float = 0.8
    requires_reason: bool = False


# A PolicyTable maps each WriteSource to the rule that applies to it.
PolicyTable = dict[WriteSource, PolicyRule]


def evaluate_write_policy(
    *,
    policy: PolicyTable,
    source: WriteSource,
    force: bool,
    chunks: list[PersonaChunk],
    reason: str | None,
    store_kind: str,
    persona_id: str,
) -> None:
    """Raise the appropriate domain exception if the write violates policy.

    Args:
        policy: The store's policy table (one entry per ``WriteSource``).
        source: The source of this write.
        force: Whether the caller asserted ``force=True``.
        chunks: The chunks being written. Used for per-source extra checks
            (e.g., confidence threshold).
        reason: The caller's reason. Required for persona_self writes when
            the rule says so.
        store_kind: For error context.
        persona_id: For error context.

    Raises:
        RuntimeWriteForbiddenError: System/user writes rejected by policy.
        PersonaSelfWriteForbiddenError: Persona_self writes rejected.
    """
    rule = policy.get(source)
    if rule is None:
        msg = "no policy rule for source"
        raise RuntimeWriteForbiddenError(msg, context={"store": store_kind, "source": str(source)})

    if rule.decision is PolicyDecision.ACCEPT:
        return

    if rule.decision is PolicyDecision.REJECT:
        msg = "write rejected by store policy"
        ctx = {"store": store_kind, "source": str(source), "persona_id": persona_id}
        if source is WriteSource.PERSONA_SELF:
            raise PersonaSelfWriteForbiddenError(msg, context=ctx)
        raise RuntimeWriteForbiddenError(msg, context=ctx)

    # FORCE_ONLY from here on.
    if not force:
        msg = "write requires force=True for this source on this store"
        ctx = {"store": store_kind, "source": str(source), "persona_id": persona_id}
        if source is WriteSource.PERSONA_SELF:
            raise PersonaSelfWriteForbiddenError(msg, context=ctx)
        raise RuntimeWriteForbiddenError(msg, context=ctx)

    # Extra checks apply only to persona_self.
    if source is WriteSource.PERSONA_SELF:
        if rule.requires_reason and not (reason and reason.strip()):
            msg = "persona_self write requires a non-empty reason"
            raise PersonaSelfWriteForbiddenError(
                msg,
                context={"store": store_kind, "persona_id": persona_id},
            )
        if rule.persona_self_requirement is PersonaSelfRequirement.CONFIDENCE_THRESHOLD:
            _check_confidence_threshold(
                chunks,
                threshold=rule.persona_self_confidence_threshold,
                store_kind=store_kind,
                persona_id=persona_id,
            )
        elif rule.persona_self_requirement is PersonaSelfRequirement.EPISTEMIC_TAG_SET:
            _check_epistemic_tag_set(chunks, store_kind=store_kind, persona_id=persona_id)


def _check_confidence_threshold(
    chunks: list[PersonaChunk],
    *,
    threshold: float,
    store_kind: str,
    persona_id: str,
) -> None:
    for chunk in chunks:
        raw = chunk.metadata.get("confidence")
        if raw is None:
            msg = "persona_self write requires confidence metadata"
            raise PersonaSelfWriteForbiddenError(
                msg,
                context={
                    "store": store_kind,
                    "persona_id": persona_id,
                    "chunk_id": chunk.id,
                },
            )
        try:
            confidence = float(raw)
        except (TypeError, ValueError) as exc:
            msg = "persona_self write confidence is not a number"
            raise PersonaSelfWriteForbiddenError(
                msg,
                context={
                    "store": store_kind,
                    "persona_id": persona_id,
                    "chunk_id": chunk.id,
                    "raw": str(raw),
                },
            ) from exc
        if confidence < threshold:
            msg = "persona_self write confidence below threshold"
            raise PersonaSelfWriteForbiddenError(
                msg,
                context={
                    "store": store_kind,
                    "persona_id": persona_id,
                    "chunk_id": chunk.id,
                    "confidence": f"{confidence:.3f}",
                    "threshold": f"{threshold:.3f}",
                },
            )


def _check_epistemic_tag_set(
    chunks: list[PersonaChunk],
    *,
    store_kind: str,
    persona_id: str,
) -> None:
    for chunk in chunks:
        tag = chunk.metadata.get("epistemic", "").strip()
        if not tag:
            msg = "persona_self write requires non-empty epistemic tag"
            raise PersonaSelfWriteForbiddenError(
                msg,
                context={
                    "store": store_kind,
                    "persona_id": persona_id,
                    "chunk_id": chunk.id,
                },
            )
