"""Persona CRUD + memory-store population (spec 08, T07, D-08-8).

Business logic for the persona endpoints, decoupled from FastAPI. Each function
takes the RLS-scoped resources the route resolves (the connection + the RLS
engine), so every DB access — the ``personas`` row AND the memory-chunk writes
through the typed stores — runs under the authenticated tenant's scope (D-08-1).

On create, the persona's identity/self_facts/worldview entries are embedded and
written to ``memory_chunks`` via the four typed stores composing
``PostgresBackend`` — exactly the CLI's composition, but RLS-scoped and on
Postgres (D-08-8). Episodic starts empty (runtime-written).
"""

from __future__ import annotations

import shutil
import uuid
from typing import TYPE_CHECKING

import yaml
from persona.audit import JSONLAuditLogger
from persona.errors import PersonaError, PersonaNotFoundError
from persona.registry import PersonaRegistry
from persona.schema.persona import Persona
from persona.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona.stores.embedder import SentenceTransformerEmbedder
from persona.stores.postgres import PostgresBackend
from sqlalchemy import delete, insert, select, update

from persona_api.db.models import personas as personas_t
from persona_api.schemas import PersonaSummary

if TYPE_CHECKING:
    from pathlib import Path

    from persona.stores.embedder import Embedder
    from sqlalchemy import Engine

__all__ = [
    "create_persona",
    "default_embedder",
    "delete_persona",
    "get_persona",
    "list_personas",
    "load_persona_from_yaml",
    "summary_of",
    "update_persona",
]


def load_persona_from_yaml(yaml_str: str, *, persona_id: str, owner_id: str) -> Persona:
    """Validate a YAML string into a Persona, assigning id + owner (D-08-8).

    Raises ``PersonaError`` on malformed YAML and ``pydantic.ValidationError``
    (mapped to 422 by the handlers) on a schema-shape mismatch.
    """
    try:
        raw = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise PersonaError("invalid YAML", context={"reason": str(exc)[:200]}) from exc
    if not isinstance(raw, dict):
        raise PersonaError(
            "persona YAML must be a top-level mapping",
            context={"actual_type": type(raw).__name__},
        )
    # The API owns id + owner; the YAML's own values (if any) are overridden so
    # a tenant can't author another owner's persona.
    raw["persona_id"] = persona_id
    raw["owner_id"] = owner_id
    return Persona.model_validate(raw)  # ValidationError → 422


def _build_registry(engine: Engine, embedder: Embedder, audit_root: Path) -> PersonaRegistry:
    """Compose the four typed stores over PostgresBackend (RLS-scoped engine)."""
    backend = PostgresBackend(engine=engine, embedder=embedder)
    audit = JSONLAuditLogger(audit_root)
    stores = {
        "identity": IdentityStore(backend=backend, audit_logger=audit),
        "self_facts": SelfFactsStore(backend=backend, audit_logger=audit),
        "worldview": WorldviewStore(backend=backend, audit_logger=audit),
        "episodic": EpisodicStore(backend=backend, audit_logger=audit),
    }
    return PersonaRegistry(stores=stores, audit_logger=audit)


def create_persona(
    *,
    rls_engine: Engine,
    embedder: Embedder,
    audit_root: Path,
    owner_id: str,
    yaml_str: str,
    avatar_url: str | None = None,
) -> str:
    """Create a persona: insert the row + populate memory stores (D-08-8).

    Returns the generated ``persona_id`` (always set).

    ``rls_engine`` is RLS-scoped to ``owner_id`` via the request contextvar (the
    pool checkout listener, D-08-1). The ``personas`` row is committed FIRST (its
    own transaction) so the subsequent memory-chunk writes — which the typed
    stores' ``PostgresBackend`` runs on its own pooled connection — satisfy the
    ``memory_chunks.persona_id`` FK (a separate connection can't see an
    uncommitted row; verified in research). Both transactions carry the tenant
    GUC, so RLS holds throughout.
    """
    persona_id = f"persona_{uuid.uuid4().hex}"
    persona = load_persona_from_yaml(yaml_str, persona_id=persona_id, owner_id=owner_id)

    with rls_engine.begin() as conn:
        conn.execute(
            insert(personas_t).values(
                id=persona_id,
                owner_id=owner_id,
                yaml=yaml_str,
                schema_version=persona.schema_version,
                avatar_url=avatar_url,
            )
        )
    # Persona row committed → its FK target is visible to the store connections.
    registry = _build_registry(rls_engine, embedder, audit_root)
    registry.load_persona(persona)
    return persona_id


def update_persona(
    *,
    rls_engine: Engine,
    embedder: Embedder,
    audit_root: Path,
    owner_id: str,
    persona_id: str,
    yaml_str: str,
    avatar_url: str | None = None,
) -> None:
    """Replace a persona's YAML (re-validated) and re-index its memory.

    ``avatar_url`` is updated only when provided (``None`` leaves it untouched —
    a PATCH semantics for the presentation field).
    """
    persona = load_persona_from_yaml(yaml_str, persona_id=persona_id, owner_id=owner_id)
    values: dict[str, object] = {"yaml": yaml_str, "schema_version": persona.schema_version}
    if avatar_url is not None:
        values["avatar_url"] = avatar_url
    with rls_engine.begin() as conn:
        result = conn.execute(
            update(personas_t)
            .where(personas_t.c.id == persona_id)
            .values(**values)
            .returning(personas_t.c.id)
        )
        if result.first() is None:
            raise PersonaNotFoundError("persona not found", context={"id": persona_id})
    registry = _build_registry(rls_engine, embedder, audit_root)
    registry.load_persona(persona)


def get_persona(*, rls_engine: Engine, persona_id: str) -> dict[str, object]:
    """Return a persona row (RLS-scoped → 404 if not the caller's)."""
    with rls_engine.begin() as conn:
        row = (
            conn.execute(select(personas_t).where(personas_t.c.id == persona_id)).mappings().first()
        )
    if row is None:
        raise PersonaNotFoundError("persona not found", context={"id": persona_id})
    return dict(row)


def list_personas(*, rls_engine: Engine, limit: int, offset: int) -> list[dict[str, object]]:
    """List the caller's personas (RLS-scoped), paginated."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(personas_t)
                .order_by(personas_t.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def delete_persona(
    *, rls_engine: Engine, persona_id: str, workspace_root: Path | None = None, owner_id: str
) -> None:
    """Delete a persona (cascades conversations + memory via FK + workspace files).

    Spec 13 D-13-4 cascade-before-DB: rmtree the persona's workspace subtree
    BEFORE the DB DELETE fires, so a partial failure leaves orphan DB rows
    (recoverable by re-running the delete) rather than orphan files (silently
    leaking storage). Per D-13-4-v0.1-coarse-cascade, this is the only
    workspace cleanup point in v0.1 — per-conversation cleanup defers to the
    ``messages.images`` JSONB column migration.
    """
    if workspace_root is not None:
        persona_root = workspace_root / owner_id / persona_id
        if persona_root.exists():
            shutil.rmtree(persona_root, ignore_errors=True)
    with rls_engine.begin() as conn:
        result = conn.execute(
            delete(personas_t).where(personas_t.c.id == persona_id).returning(personas_t.c.id)
        )
        if result.first() is None:
            raise PersonaNotFoundError("persona not found", context={"id": persona_id})


def default_embedder(model_name: str) -> SentenceTransformerEmbedder:
    """The production embedder (bge-small-en-v1.5, 384-dim)."""
    return SentenceTransformerEmbedder(model_name=model_name)


def summary_of(row: dict[str, object]) -> PersonaSummary:
    """Build a list-view summary from a persona row (name/role from the YAML)."""
    name, role = "", ""
    try:
        parsed = yaml.safe_load(str(row["yaml"]))
        identity = parsed.get("identity", {}) if isinstance(parsed, dict) else {}
        name = str(identity.get("name", ""))
        role = str(identity.get("role", ""))
    except (yaml.YAMLError, AttributeError):
        pass  # a malformed stored YAML still lists, just without name/role
    avatar = row.get("avatar_url")
    return PersonaSummary(
        id=str(row["id"]),
        name=name,
        role=role,
        avatar_url=str(avatar) if avatar is not None else None,
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )
