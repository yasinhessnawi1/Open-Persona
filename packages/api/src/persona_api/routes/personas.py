"""Persona CRUD + LLM-assisted authoring routes (spec 08, T07, §5.1).

Every route depends on ``get_current_user`` (which sets the RLS contextvar, so
the service's engine transactions are tenant-scoped — D-08-1) and reads the
RLS engine + embedder from ``app.state`` (attached by the lifespan). The
business logic lives in the services; the routes are thin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.errors import RefinementLimitError
from persona_api.middleware.rate_limit import rate_limit
from persona_api.schemas import (
    AuthoringDraft,
    AuthorPersonaRequest,
    CreatePersonaRequest,
    PersonaCapabilities,
    PersonaDetail,
    PersonaSummary,
    RefinePersonaRequest,
    UpdatePersonaRequest,
)
from persona_api.services import (
    audit_service,
    authoring_service,
    catalog_service,
    credits_service,
    persona_service,
)

if TYPE_CHECKING:
    from persona_runtime.tier import TierRegistry

# The 3-round refinement cap (D-10-5): the UI owns the counter, the server is
# the backstop. `round` is the count of refinements already applied.
_MAX_REFINE_ROUNDS = 3

router = APIRouter(prefix="/v1/personas", tags=["personas"])


def _tier_registry(request: Request) -> TierRegistry | None:
    """Return the app-scoped :class:`TierRegistry` if the runtime is wired.

    The composition root (``app.py`` lifespan) mounts ``app.state.tier_registry``
    when a runtime backend is configured. Tests that don't wire the runtime
    leave the attribute unset; the persona-detail surface stays usable and
    just omits :attr:`PersonaDetail.capabilities`.
    """
    return getattr(request.app.state, "tier_registry", None)


def _capabilities_from_registry(
    tier_registry: TierRegistry | None,
) -> PersonaCapabilities | None:
    """Hydrate :class:`PersonaCapabilities` from the runtime registry.

    Returns ``None`` if the registry was not wired (test paths / composition
    roots without a runtime). At v0.1 capability is deployment-derived per
    D-F3-X-deployment-vs-persona-capability-framing — the same answer applies
    to every persona under a given deployment because the registry is
    app-scoped. Reads through the public
    :meth:`TierRegistry.supports_vision_for` contract
    (D-F3-X-tier-registry-public-contract) so capability-matrix migrations
    don't ripple here.
    """
    if tier_registry is None:
        return None
    tier_names = tier_registry.configured_tier_names
    vision = any(tier_registry.supports_vision_for(name) for name in tier_names)
    return PersonaCapabilities(vision=vision, configured_tiers=tier_names)


def _persona_detail(
    row: dict[str, object],
    *,
    tier_registry: TierRegistry | None,
) -> PersonaDetail:
    avatar = row.get("avatar_url")
    return PersonaDetail(
        id=str(row["id"]),
        yaml=str(row["yaml"]),
        schema_version=str(row["schema_version"]),
        avatar_url=str(avatar) if avatar is not None else None,
        capabilities=_capabilities_from_registry(tier_registry),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonaDetail)
async def create_persona(
    body: CreatePersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PersonaDetail:
    """Create a persona from YAML; populate its memory stores (D-08-8)."""
    persona_id = persona_service.create_persona(
        rls_engine=request.app.state.rls_engine,
        embedder=request.app.state.embedder,
        audit_root=request.app.state.audit_root,
        owner_id=user.id,
        yaml_str=body.yaml,
        avatar_url=body.avatar_url,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="persona.create",
        target=persona_id,
    )
    row = persona_service.get_persona(
        rls_engine=request.app.state.rls_engine, persona_id=persona_id
    )
    return _persona_detail(row, tier_registry=_tier_registry(request))


@router.post("/author", response_model=AuthoringDraft, dependencies=[Depends(rate_limit("author"))])
async def author_persona(
    body: AuthorPersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthoringDraft:
    """Generate a DRAFT persona from a description for review (D-10-2).

    Returns the draft envelope (YAML + clarifying questions + prompt version) —
    it does NOT create a persona row. The user reviews/refines, then saves via
    ``POST /v1/personas``. The flat authoring credit is deducted per call (the
    cost is the frontier-model call, not a row; D-10-8).
    """
    # Pre-flight credit guard (D-11-12 / spec 11 §5).
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    backend = request.app.state.tier_registry.get(request.app.state.authoring_tier)
    draft = await authoring_service.generate_authoring_draft(
        backend,
        body.description,
        [name for name, _ in catalog_service.list_tools()],
        [name for name, _ in catalog_service.list_skills()],
    )
    _deduct_and_audit(
        request, user, "persona.author", draft.prompt_version, reason="persona_authoring"
    )
    return draft


@router.post(
    "/author/refine", response_model=AuthoringDraft, dependencies=[Depends(rate_limit("author"))]
)
async def refine_persona(
    body: RefinePersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthoringDraft:
    """Refine a draft persona by answering a clarifying question (§4, D-10-2).

    Stateless: the request carries ``round`` (refinements already applied); the
    server rejects ``round >= 3`` as the backstop on the 3-round cap (D-10-5).
    Returns the updated draft envelope; deducts the flat authoring credit.
    """
    if body.round >= _MAX_REFINE_ROUNDS:
        raise RefinementLimitError(
            "refinement limit reached",
            context={"round": str(body.round), "max_rounds": str(_MAX_REFINE_ROUNDS)},
        )
    # Pre-flight credit guard (D-11-12 / spec 11 §5).
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    backend = request.app.state.tier_registry.get(request.app.state.authoring_tier)
    draft = await authoring_service.refine_authoring_draft(
        backend,
        body.current_yaml,
        body.question,
        body.answer,
        [name for name, _ in catalog_service.list_tools()],
        [name for name, _ in catalog_service.list_skills()],
    )
    _deduct_and_audit(
        request,
        user,
        "persona.author_refine",
        draft.prompt_version,
        reason="persona_authoring_refine",
    )
    return draft


def _deduct_and_audit(
    request: Request,
    user: AuthenticatedUser,
    action: str,
    prompt_version: str,
    *,
    reason: str,
) -> None:
    """Deduct the flat authoring credit + record a targetless audit event (D-10-8).

    Author/refine create no persona row, so the audit ``target`` is empty; the
    eventual ``POST /v1/personas`` audits ``persona.create`` against the real id.
    """
    credits_service.deduct(
        rls_engine=request.app.state.rls_engine,
        user_id=user.id,
        amount=request.app.state.config.authoring_credit_cost,
        reason=reason,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action=action,
        target="",
        metadata={"prompt_version": prompt_version},
    )


@router.get("", response_model=list[PersonaSummary])
async def list_personas(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
    limit: int = 50,
    offset: int = 0,
) -> list[PersonaSummary]:
    """List the caller's personas (paginated; RLS-scoped)."""
    rows = persona_service.list_personas(
        rls_engine=request.app.state.rls_engine, limit=min(limit, 200), offset=offset
    )
    return [persona_service.summary_of(r) for r in rows]


@router.get("/{persona_id}", response_model=PersonaDetail)
async def get_persona(
    persona_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
) -> PersonaDetail:
    """Get a persona's YAML + metadata (404 if not the caller's)."""
    row = persona_service.get_persona(
        rls_engine=request.app.state.rls_engine, persona_id=persona_id
    )
    return _persona_detail(row, tier_registry=_tier_registry(request))


@router.patch("/{persona_id}", response_model=PersonaDetail)
async def update_persona(
    persona_id: str,
    body: UpdatePersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PersonaDetail:
    """Replace a persona's YAML (re-validated) and re-index its memory."""
    persona_service.update_persona(
        rls_engine=request.app.state.rls_engine,
        embedder=request.app.state.embedder,
        audit_root=request.app.state.audit_root,
        owner_id=user.id,
        persona_id=persona_id,
        yaml_str=body.yaml,
        avatar_url=body.avatar_url,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="persona.update",
        target=persona_id,
    )
    row = persona_service.get_persona(
        rls_engine=request.app.state.rls_engine, persona_id=persona_id
    )
    return _persona_detail(row, tier_registry=_tier_registry(request))


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """Delete a persona + all its conversations and memory (cascade)."""
    persona_service.delete_persona(
        rls_engine=request.app.state.rls_engine,
        persona_id=persona_id,
        workspace_root=getattr(request.app.state, "workspace_root", None),
        owner_id=user.id,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="persona.delete",
        target=persona_id,
    )
