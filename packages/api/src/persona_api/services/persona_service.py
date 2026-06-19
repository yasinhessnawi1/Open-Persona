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
from persona.language_capability import serviceability_warning
from persona.logging import get_logger
from persona.registry import PersonaRegistry
from persona.schema.persona import Persona
from persona.schema.safety import SAFETY_CONSTRAINT, ensure_safety_constraint
from persona.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona.stores.embedder import SentenceTransformerEmbedder
from persona.stores.postgres import PostgresBackend
from sqlalchemy import delete, func, insert, select, update

from persona_api.db.models import conversations as conversations_t
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
    "set_avatar_url",
    "summary_of",
    "update_persona",
]


_LOG = get_logger("services.persona")


def _warn_if_language_unserviceable(persona: Persona) -> None:
    """Author-time voice-language warning (Spec 32 D-32-4).

    Non-blocking: a persona whose declared language the configured voice
    providers can't serve still saves, but the author is warned (the persona's
    calls will fall back to English) before a call rather than during one. The
    complement to the call-time soft-fallback.
    """
    warning = serviceability_warning(persona.identity.language_default)
    if warning is not None:
        _LOG.warning(
            "persona declares an unserviceable voice language (persona_id={pid}): {msg}",
            pid=persona.persona_id or "",
            msg=warning,
        )


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


def _guard_safety(persona: Persona, yaml_str: str) -> tuple[Persona, str]:
    """Guarantee the mandatory safety constraint on the persona AND the stored YAML.

    Spec 36, D-36-safety-server: direct-create posts a structured YAML with no
    model in the loop, so the drafter prompt's instruction to include the safety
    constraint cannot be relied on. This is the enforcement floor for *every*
    create/update path. The constraint must end up in the **stored** YAML — the
    runtime re-loads the persona from it, so guarding only the in-memory object
    would leave the persona unsafe on its next load.

    Idempotent + churn-free: when the constraint is already present (every
    prebuilt starter and drafter output), the original ``yaml_str`` is returned
    unchanged. Only a submission that stripped the constraint is re-dumped — and
    only then does the stored representation change — re-injecting it as the
    first constraint of the original mapping (authored shape preserved).
    """
    guarded = ensure_safety_constraint(persona)
    if guarded is persona:
        return persona, yaml_str
    raw = yaml.safe_load(yaml_str)
    identity = raw.setdefault("identity", {})
    existing = identity.get("constraints") or []
    identity["constraints"] = [SAFETY_CONSTRAINT, *existing]
    guarded_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    _LOG.warning(
        "re-asserted the mandatory safety constraint on a persona that omitted it "
        "(persona_id={pid})",
        pid=persona.persona_id or "",
    )
    return guarded, guarded_yaml


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
    persona, yaml_str = _guard_safety(persona, yaml_str)
    _warn_if_language_unserviceable(persona)

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
    persona, yaml_str = _guard_safety(persona, yaml_str)
    _warn_if_language_unserviceable(persona)
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


def set_avatar_url(*, rls_engine: Engine, persona_id: str, avatar_url: str) -> None:
    """Set a persona's ``avatar_url`` presentation field (Spec 29 D-29-3).

    A narrow, RLS-scoped write for the build-time avatar auto-generation hook:
    unlike :func:`update_persona` it does NOT re-validate the YAML or re-index
    memory — it touches only the ``avatar_url`` column. Silent if the row is
    absent (the create transaction just committed it; a concurrent delete is a
    no-op rather than an error, per the idempotency standard). The auto-gen hook
    runs only when ``avatar_url`` was null at create, so this never overwrites a
    user-supplied avatar (D-29 criterion 6).
    """
    with rls_engine.begin() as conn:
        conn.execute(
            update(personas_t).where(personas_t.c.id == persona_id).values(avatar_url=avatar_url)
        )


def set_voice(*, rls_engine: Engine, persona_id: str, provider: str, voice_id: str) -> None:
    """Inject ``identity.voice`` into a persona's stored YAML (Issue 1, narrow write).

    Reads the current YAML, sets ``identity.voice`` to the ``"provider:voice_id"``
    shorthand the schema accepts (normalised to a ``CatalogueVoice`` at load), and
    rewrites ONLY the ``yaml`` column — no memory re-index, since the voice is read
    from the persona definition at synthesis time, never retrieved semantically.
    Silent if the row is absent or the YAML has no ``identity`` mapping. Mirrors
    :func:`set_avatar_url`'s narrow-write shape; the build-time voice
    auto-assignment hook is the only caller and runs only when ``identity.voice``
    was unset (it never overwrites a builder's chosen voice).
    """
    with rls_engine.begin() as conn:
        row = conn.execute(select(personas_t.c.yaml).where(personas_t.c.id == persona_id)).first()
        if row is None:
            return
        raw = yaml.safe_load(row[0])
        if not isinstance(raw, dict) or not isinstance(raw.get("identity"), dict):
            return
        raw["identity"]["voice"] = f"{provider}:{voice_id}"
        new_yaml = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        conn.execute(update(personas_t).where(personas_t.c.id == persona_id).values(yaml=new_yaml))


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


def conversation_counts(*, rls_engine: Engine) -> dict[str, int]:
    """Conversations per persona for the caller (one RLS-scoped GROUP BY).

    Spec 35: feeds ``PersonaSummary.conversation_count`` for the whole library
    page in a single query — never per-persona. RLS scopes it to the owner.
    """
    with rls_engine.begin() as conn:
        rows = conn.execute(
            select(conversations_t.c.persona_id, func.count().label("n")).group_by(
                conversations_t.c.persona_id
            )
        ).all()
    return {str(persona_id): int(n) for persona_id, n in rows}


def conversation_count_for(*, rls_engine: Engine, persona_id: str) -> int:
    """Conversation count for one persona (RLS-scoped) — the episodic source."""
    with rls_engine.begin() as conn:
        n = conn.execute(
            select(func.count())
            .select_from(conversations_t)
            .where(conversations_t.c.persona_id == persona_id)
        ).scalar_one()
    return int(n)


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
    """The production embedder (bge-small-en-v1.5, 384-dim).

    Pinned to CPU (matching persona-voice's agent embedder): ``device="auto"``
    selects Apple MPS on a Mac, where a lazy/threaded device-move can raise
    "Cannot copy out of meta tensor" and the load is slower; bge-small encodes in
    <10ms on CPU, so CPU is both robust and fast enough for the create-time
    memory-population pass.
    """
    return SentenceTransformerEmbedder(model_name=model_name, device="cpu")


def summary_of(row: dict[str, object], *, conversation_count: int = 0) -> PersonaSummary:
    """Build a list-view summary from a persona row.

    name/role + the Spec-35 capability glance (language + tools/skills/
    constraints counts) all come from the SAME stored YAML this row already
    carries — no extra query. ``conversation_count`` is supplied by the caller
    (one GROUP-BY for the whole page, never per persona).
    """
    name, role = "", ""
    language = "en"
    tools_count = skills_count = constraints_count = 0
    try:
        parsed = yaml.safe_load(str(row["yaml"]))
        if isinstance(parsed, dict):
            identity = parsed.get("identity", {})
            identity = identity if isinstance(identity, dict) else {}
            name = str(identity.get("name", ""))
            role = str(identity.get("role", ""))
            language = _language_of(identity.get("language_default"))
            constraints_count = _len_of(identity.get("constraints"))
            tools_count = _len_of(parsed.get("tools"))
            skills_count = _len_of(parsed.get("skills"))
    except (yaml.YAMLError, AttributeError):
        pass  # a malformed stored YAML still lists, just without the extras
    avatar = row.get("avatar_url")
    return PersonaSummary(
        id=str(row["id"]),
        name=name,
        role=role,
        avatar_url=str(avatar) if avatar is not None else None,
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
        language=language,
        tools_count=tools_count,
        skills_count=skills_count,
        constraints_count=constraints_count,
        conversation_count=conversation_count,
    )


def _len_of(value: object) -> int:
    """Length of a YAML list field, or 0 when absent/malformed."""
    return len(value) if isinstance(value, list) else 0


def _language_of(value: object) -> str:
    """ISO-639-1 language code from the YAML ``language_default``.

    YAML's bool keywords bite here: an unquoted ``no`` (Norwegian's own ISO
    code!) parses to ``False`` and ``yes`` to ``True``. Map those back so a
    Norwegian persona reads as ``no``, not ``False``. Empty/None → ``en``.
    """
    if value is False:
        return "no"
    if value is True:
        return "yes"
    return str(value) if value else "en"
