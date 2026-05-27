"""PersonaRegistry — load YAML, validate, index author-time chunks.

The registry is the bridge between a persona's static YAML document and the
four runtime stores. It does NOT own the stores' lifecycle (the caller
constructs and injects them) — it just orchestrates the loading step.

Per spec 01 §10: ``load(path)`` is idempotent. Re-loading the same YAML
into the same stores must not produce duplicate chunks. We achieve this by
keying each author-time chunk on a deterministic ``logical_id`` derived
from the source field + index, so a second load sees the existing logical
id and short-circuits via :func:`current_version`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.errors import StoreNotFoundError
from persona.logging import get_logger
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource, make_chunk_id
from persona.schema.persona import Persona
from persona.stores.versioning import current_version

if TYPE_CHECKING:
    from pathlib import Path

    from persona.audit import AuditLogger
    from persona.stores.base import TypedStore

__all__ = ["PersonaRegistry"]

_REQUIRED_STORE_KINDS: tuple[str, ...] = ("identity", "self_facts", "worldview", "episodic")


class PersonaRegistry:
    """Load and index personas into a set of typed stores.

    Args:
        stores: Map from store kind (``"identity"``, ``"self_facts"``, ...)
            to the concrete :class:`TypedStore` instance. Must contain all
            four kinds; missing kinds raise :class:`StoreNotFoundError` at
            construction.
        audit_logger: Audit sink. Required for visibility into who loaded
            which persona and when.
    """

    def __init__(
        self,
        *,
        stores: dict[str, TypedStore],
        audit_logger: AuditLogger,
    ) -> None:
        missing = [k for k in _REQUIRED_STORE_KINDS if k not in stores]
        if missing:
            msg = "missing required store kinds"
            raise StoreNotFoundError(msg, context={"missing": ",".join(missing)})
        self._stores = stores
        self._audit = audit_logger
        self._log = get_logger("registry")

    def load(self, path: Path) -> Persona:
        """Load a persona YAML and index its author-time chunks.

        ``load`` is idempotent: rerunning it on the same YAML against the
        same stores does not duplicate chunks. Identity chunks are indexed
        only on first load (the identity store rejects subsequent writes
        by policy); the other three stores see versioned writes that
        short-circuit when the same logical id already has a head with
        identical content.

        Args:
            path: Filesystem path to the persona YAML.

        Returns:
            The validated :class:`Persona`. The caller can keep it around
            to access identity/self_facts/etc. without re-reading the file.
        """
        persona = Persona.from_yaml(path)
        assert persona.persona_id is not None  # set by from_yaml
        persona_id = persona.persona_id
        self._log.info(
            "loading persona persona_id={persona_id} path={path}",
            persona_id=persona_id,
            path=str(path),
        )

        # Identity is immutable at runtime; the registry indexes it directly
        # via the backend on first load.
        self._index_identity_if_first_load(persona)
        self._index_versioned("self_facts", persona)
        self._index_versioned("worldview", persona)
        self._index_versioned("episodic", persona)
        return persona

    # ----- helpers ---------------------------------------------------------

    def _index_identity_if_first_load(self, persona: Persona) -> None:
        """Index identity chunks the first time we see this persona.

        The identity store rejects writes by policy, so the registry talks
        to its backend directly. Subsequent loads find existing identity
        chunks and short-circuit.
        """
        store = self._stores["identity"]
        persona_id = persona.persona_id or ""
        existing = store.get_all(persona_id, include_superseded=True)
        if existing:
            return
        identity_chunks = self._build_identity_chunks(persona)
        if not identity_chunks:
            return
        # Bypass the policy enforcement on identity by going through the
        # backend directly — this is the registry's privileged loading path.
        store._backend.upsert(  # noqa: SLF001 — registry is the privileged loader
            persona_id=persona_id,
            store_kind=store.STORE_KIND,
            chunks=identity_chunks,
        )

    def _build_identity_chunks(self, persona: Persona) -> list[PersonaChunk]:
        persona_id = persona.persona_id or ""
        now = datetime.now(UTC)
        identity = persona.identity
        texts: list[tuple[str, dict[str, str]]] = [
            (
                f"name: {identity.name}",
                {"field": "name"},
            ),
            (
                f"role: {identity.role}",
                {"field": "role"},
            ),
            (
                f"background: {identity.background}",
                {"field": "background"},
            ),
            (
                f"language_default: {identity.language_default}",
                {"field": "language_default"},
            ),
        ]
        for constraint in identity.constraints:
            texts.append((f"constraint: {constraint}", {"field": "constraint"}))

        chunks: list[PersonaChunk] = []
        for index, (text, meta) in enumerate(texts):
            chunk_id = make_chunk_id(persona_id, "identity", index)
            chunks.append(
                PersonaChunk(
                    id=chunk_id,
                    text=text,
                    metadata=meta,
                    created_at=now,
                ),
            )
        return chunks

    def _index_versioned(self, store_kind: str, persona: Persona) -> None:
        store = self._stores[store_kind]
        persona_id = persona.persona_id or ""
        chunks_to_write = list(self._build_versioned_chunks(store_kind, persona))
        if not chunks_to_write:
            return

        # Idempotency: skip chunks whose logical_id already has a head with
        # identical content_hash. We rely on the store's existing state to
        # decide.
        existing = store.get_all(persona_id, include_superseded=True)

        deduped: list[PersonaChunk] = []
        for chunk in chunks_to_write:
            head = (
                current_version(existing, chunk.provenance.logical_id)
                if chunk.provenance is not None
                else None
            )
            if head is not None and head.content_hash == chunk.content_hash:
                continue  # same content already on disk — skip
            deduped.append(chunk)

        if not deduped:
            return
        store.write(
            persona_id,
            deduped,
            source=WriteSource.USER,
            written_by=persona.owner_id or "author",
            reason="initial persona load",
        )

    def _build_versioned_chunks(self, store_kind: str, persona: Persona) -> list[PersonaChunk]:
        persona_id = persona.persona_id or ""
        now = datetime.now(UTC)
        if store_kind == "self_facts":
            return [
                self._chunk_with_provenance(
                    persona_id=persona_id,
                    store_kind=store_kind,
                    index=i,
                    text=f"self_fact: {sf.fact}",
                    metadata={"confidence": f"{sf.confidence:.3f}"},
                    now=now,
                )
                for i, sf in enumerate(persona.self_facts)
            ]
        if store_kind == "worldview":
            return [
                self._chunk_with_provenance(
                    persona_id=persona_id,
                    store_kind=store_kind,
                    index=i,
                    text=f"worldview: {wv.claim}",
                    metadata={
                        "domain": wv.domain,
                        "epistemic": wv.epistemic,
                        "confidence": f"{wv.confidence:.3f}",
                        "valid_time": wv.valid_time,
                    },
                    now=now,
                )
                for i, wv in enumerate(persona.worldview)
            ]
        if store_kind == "episodic":
            return [
                self._chunk_with_provenance(
                    persona_id=persona_id,
                    store_kind=store_kind,
                    index=i,
                    text=ep.content,
                    metadata={"importance": f"{ep.importance:.3f}"},
                    now=ep.created_at,
                )
                for i, ep in enumerate(persona.episodic)
            ]
        msg = f"unknown store kind {store_kind!r}"
        raise StoreNotFoundError(msg, context={"store_kind": store_kind})

    def _chunk_with_provenance(
        self,
        *,
        persona_id: str,
        store_kind: str,
        index: int,
        text: str,
        metadata: dict[str, str],
        now: datetime,
    ) -> PersonaChunk:
        chunk_id = make_chunk_id(persona_id, store_kind, index)
        return PersonaChunk(
            id=chunk_id,
            text=text,
            metadata=metadata,
            created_at=now,
            provenance=ChunkProvenance(
                source=WriteSource.USER,
                logical_id=chunk_id,  # D-01-8: logical_id = id on first write
                version=1,
                written_at=now,
                written_by="author",
                reason="initial persona load",
            ),
        )
