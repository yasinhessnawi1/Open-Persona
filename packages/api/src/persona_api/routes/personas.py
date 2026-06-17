"""Persona CRUD + LLM-assisted authoring routes (spec 08, T07, §5.1).

Every route depends on ``get_current_user`` (which sets the RLS contextvar, so
the service's engine transactions are tenant-scoped — D-08-1) and reads the
RLS engine + embedder from ``app.state`` (attached by the lifespan). The
business logic lives in the services; the routes are thin.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from persona.imagegen import ContentRejectedError, ImageGenError, craft_avatar_prompt
from persona.logging import get_logger
from persona.tools.audit import JSONLToolAuditLogger, ToolAuditEvent

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.errors import RefinementLimitError
from persona_api.imagegen import service as imagegen_service
from persona_api.middleware.rate_limit import rate_limit
from persona_api.schemas import (
    AuthoringDraft,
    AuthorPersonaRequest,
    CreatePersonaRequest,
    GrantToolRequest,
    PersonaCapabilities,
    PersonaDetail,
    PersonaSummary,
    RefinePersonaRequest,
    SetConsentRequest,
    ToolRecommendationResponse,
    UpdatePersonaRequest,
)
from persona_api.services import (
    audit_service,
    authoring_service,
    catalog_service,
    consent_service,
    credits_service,
    persona_service,
    tool_consent_service,
    voice_assignment_service,
)

if TYPE_CHECKING:
    from persona_runtime.tier import TierRegistry

# The 3-round refinement cap (D-10-5): the UI owns the counter, the server is
# the backstop. `round` is the count of refinements already applied.
_MAX_REFINE_ROUNDS = 3

#: Fallback avatar-gen wall-clock bound if app.state didn't thread the config
#: value (e.g. a test that builds the app without the Spec-29 lifespan line).
#: The authoritative value is ``APIConfig.avatar_gen_timeout_s`` (D-29-3).
_DEFAULT_AVATAR_GEN_TIMEOUT_S = 25.0

_LOG = get_logger("routes.personas")

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
    consent = row.get("consent_to_auto_dispatch")
    return PersonaDetail(
        id=str(row["id"]),
        yaml=str(row["yaml"]),
        schema_version=str(row["schema_version"]),
        avatar_url=str(avatar) if avatar is not None else None,
        capabilities=_capabilities_from_registry(tier_registry),
        consent_to_auto_dispatch=bool(consent) if consent is not None else None,
        consent_updated_at=row.get("consent_updated_at"),  # type: ignore[arg-type]
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )


def _emit_avatar_build_audit(
    audit: JSONLToolAuditLogger,
    persona_id: str,
    *,
    reason: str,
    detail: str | None = None,
) -> None:
    """Emit the build-hook's own fail-soft audit (backend-absent / timeout / unexpected).

    Covers the two outcomes ``generate_avatar`` cannot reach (no backend
    configured, and the wall-clock timeout that cancels it mid-flight) plus a
    defensive catch-all. The generation-specific outcomes (hard-line / provider
    rejection / provider error) are audited inside ``generate_avatar`` itself.
    Tagged zero-cost system event (D-29-2), JSONL, no migration.
    """
    metadata: dict[str, str] = {
        "outcome": "error",
        "reason": reason,
        "system_initiated": "true",
        "credits_charged": "0",
    }
    if detail is not None:
        metadata["detail"] = detail
    audit.emit(
        ToolAuditEvent(
            timestamp=datetime.now(UTC),
            persona_id=persona_id,
            tool_name="generate_avatar",
            action="execute",
            resource="build_hook",
            is_error=True,
            metadata=metadata,
        )
    )


async def _maybe_generate_avatar(
    request: Request, *, owner_id: str, persona_id: str, yaml_str: str
) -> None:
    """Build-time avatar auto-generation hook (Spec 29 D-29-3, fail-soft).

    Runs after the persona row is committed, only when the builder supplied no
    avatar (the caller guards on ``body.avatar_url is None``). Crafts a
    demographic-safe prompt (D-29-1), generates through the free build-time
    entry bounded by ``avatar_gen_timeout_s`` (D-29-3), and on success points
    ``avatar_url`` at the served uploads path. **Every failure mode fail-softs
    to ``avatar_url=null`` and audits — this coroutine never raises into the
    create path** (D-29-X-fail-soft): a persona must never fail to exist because
    its avatar could not be drawn. F1's default renders until one is set.
    """
    state = request.app.state
    audit = JSONLToolAuditLogger(state.audit_root)

    # Backend absent (no PERSONA_IMAGEGEN_API_KEY) → fail-soft + audit.
    backend = getattr(state, "image_backend", None)
    if backend is None:
        _emit_avatar_build_audit(audit, persona_id, reason="backend_not_configured")
        return

    # Re-parse the just-validated YAML to reach identity (cheap; create_persona
    # already proved it validates, so this does not raise in practice).
    persona = persona_service.load_persona_from_yaml(
        yaml_str, persona_id=persona_id, owner_id=owner_id
    )
    prompt = craft_avatar_prompt(persona.identity)
    timeout_s = getattr(state, "avatar_gen_timeout_s", _DEFAULT_AVATAR_GEN_TIMEOUT_S)

    try:
        result = await asyncio.wait_for(
            imagegen_service.generate_avatar(
                workspace_root=state.workspace_root,
                backend=backend,
                user_id=owner_id,
                persona_id=persona_id,
                prompt=prompt,
                audit_logger=audit,
            ),
            timeout=timeout_s,
        )
    except (ContentRejectedError, ImageGenError):
        # generate_avatar already audited the specific outcome (hard-line /
        # provider rejection / provider error). Fail-soft to null.
        return
    except TimeoutError:
        _emit_avatar_build_audit(audit, persona_id, reason="timeout")
        return
    except Exception as exc:  # noqa: BLE001 — avatar-gen must NEVER break create
        _emit_avatar_build_audit(audit, persona_id, reason="unexpected", detail=str(exc)[:200])
        _LOG.warning("avatar build hook unexpected error", persona_id=persona_id, error=str(exc))
        return

    workspace_path = result.images[0].workspace_path if result.images else None
    if not workspace_path:
        return  # defensive — nothing to point at
    # Store the bare workspace ref (``uploads/<blake2b>.<ext>``), NOT the full
    # route path. The uploads GET route requires Bearer auth + RLS, so the web
    # renders it through the authed-image hook (useAuthedImageBlobUrl), which
    # builds ``{API}/v1/personas/{id}/uploads/{workspace_path}`` itself. Storing
    # the full ``/v1/...`` path made the browser <img> hit the web origin
    # (relative) → 404, and it would 401 even at the API origin (no Bearer).
    # The bare ref is exactly the ``workspacePath`` the authed hook expects.
    avatar_url = workspace_path
    persona_service.set_avatar_url(
        rls_engine=state.rls_engine, persona_id=persona_id, avatar_url=avatar_url
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonaDetail)
async def create_persona(
    body: CreatePersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PersonaDetail:
    """Create a persona from YAML; populate memory stores; auto-generate an avatar.

    The avatar is generated only when the builder supplied none (D-29-3); the
    generation is fail-soft so create never fails on an imagegen problem
    (D-29-X-fail-soft). A user-supplied ``avatar_url`` always wins (criterion 6).
    """
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
    # Issue 1: auto-assign a gender/character-fitting voice when the builder
    # supplied none — so a persona isn't stuck with the global English-male
    # default. Fail-soft (never breaks create); a no-op when TTS is unconfigured.
    # Runs BEFORE avatar generation: that hook can block up to avatar_gen_timeout_s
    # (25s), and the voice pick forwards the caller's short-lived bearer token to
    # the voice service — so it must fire while that token is still fresh.
    await voice_assignment_service.maybe_assign_voice(
        request, owner_id=user.id, persona_id=persona_id, yaml_str=body.yaml
    )
    # Spec 29: auto-generate an avatar when the builder supplied none. Fail-soft.
    if body.avatar_url is None:
        await _maybe_generate_avatar(
            request, owner_id=user.id, persona_id=persona_id, yaml_str=body.yaml
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


@router.post(
    "/recommend-tools",
    response_model=ToolRecommendationResponse,
    dependencies=[Depends(rate_limit("author"))],
)
async def recommend_tools(
    body: AuthorPersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ToolRecommendationResponse:
    """Recommend a ranked tool subset for a persona description (spec 26 T09).

    Authoring-time assist: given the natural-language description, a single
    mid-tier call (D-26-2) returns up to 10 catalog-valid tool recommendations,
    highest-confidence first. Reuses the description-only ``AuthorPersonaRequest``
    body. Deducts the flat authoring credit (a mid-tier LLM call).
    """
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    backend = request.app.state.tier_registry.get("mid")
    recommendations = await authoring_service.recommend_tools_for_persona(backend, body.description)
    _deduct_and_audit(
        request,
        user,
        "persona.recommend_tools",
        authoring_service.RECOMMENDER_PROMPT_VERSION,
        reason="persona_tool_recommend",
    )
    return ToolRecommendationResponse(
        recommendations=recommendations,
        prompt_version=authoring_service.RECOMMENDER_PROMPT_VERSION,
    )


@router.post(
    "/recommend-capabilities",
    response_model=ToolRecommendationResponse,
    dependencies=[Depends(rate_limit("author"))],
)
async def recommend_capabilities(
    body: AuthorPersonaRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ToolRecommendationResponse:
    """Recommend a unified, provider-tagged capability set (spec 27 T10).

    The D-26-10 generalisation of ``/recommend-tools``: one mid-tier call ranks
    built-in tools, skills, and MCP servers together (each tagged with its
    provider), capped at the combined maximum (D-27-13). Deducts the same flat
    authoring credit (a mid-tier LLM call).
    """
    from persona.skills.catalog import BUILTIN_CATALOG

    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    backend = request.app.state.tier_registry.get("mid")
    recommendations = await authoring_service.recommend_capabilities_for_persona(
        backend,
        body.description,
        available_skills=tuple(BUILTIN_CATALOG.skills),
    )
    _deduct_and_audit(
        request,
        user,
        "persona.recommend_capabilities",
        authoring_service.RECOMMENDER_PROMPT_VERSION,
        reason="persona_capability_recommend",
    )
    return ToolRecommendationResponse(
        recommendations=recommendations,
        prompt_version=authoring_service.RECOMMENDER_PROMPT_VERSION,
    )


@router.post(
    "/{persona_id}/tools",
    response_model=PersonaDetail,
    dependencies=[Depends(rate_limit("default"))],
)
async def grant_tool(
    persona_id: str,
    body: GrantToolRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PersonaDetail:
    """Enable a tool on the persona's allow-list via runtime consent (spec 26 T11).

    Called when the user accepts a runtime tool-gap offer (T10). Adds the tool to
    the persona's ``tools`` list (persisted in the YAML column — no migration)
    and records the grant as a versioned ``persona_self`` self-fact (force +
    confidence ≥ 0.8 + reason, D-26-X-self-facts-consent-write-contract). Returns
    the updated persona detail. Idempotent: re-granting an already-enabled tool
    is a no-op that still returns 200.
    """
    from datetime import UTC, datetime

    tool_consent_service.grant_tool_consent(
        rls_engine=request.app.state.rls_engine,
        embedder=request.app.state.embedder,
        audit_root=request.app.state.audit_root,
        persona_id=persona_id,
        owner_id=user.id,
        tool_name=body.tool_name,
        written_by=user.id,
        now=datetime.now(UTC),
        turn_index=body.turn_index,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="persona.tool_grant",
        target=persona_id,
    )
    row = persona_service.get_persona(
        rls_engine=request.app.state.rls_engine, persona_id=persona_id
    )
    return _persona_detail(row, tier_registry=_tier_registry(request))


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


@router.patch("/{persona_id}/consent", response_model=PersonaDetail)
async def set_consent(
    persona_id: str,
    body: SetConsentRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PersonaDetail:
    """Set the persona's auto-dispatch consent (grant / decline / revoke).

    Spec 21 T09 (D-21-2/7/8): only this ``user``-sourced settings write may
    change consent; ``persona_self`` never can. Each transition stamps
    ``consent_updated_at`` and emits an ``AuditEvent`` naming the transition.
    """
    from datetime import UTC, datetime

    consent_service.set_consent(
        rls_engine=request.app.state.rls_engine,
        persona_id=persona_id,
        granted=body.granted,
        now=datetime.now(UTC),
    )
    transition = (
        "grant" if body.granted is True else "decline" if body.granted is False else "revoke"
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action=f"persona.consent.{transition}",
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
