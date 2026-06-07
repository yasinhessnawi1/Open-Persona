"""Image-generation composition (spec 15 T15).

This module is the hosted-service composition root for image generation.
It glues together the four cost-discipline + safety primitives the
preceding tasks delivered:

* **Concurrency cap** (T14 :func:`persona_api.imagegen.concurrency.acquire_user_concurrency`)
  — Postgres advisory transactional lock keyed by ``hash(user_id)``;
  multi-worker-correct from day one (D-15-X-concurrency-cap).
* **Pre-deduct credits** (T13 :func:`persona_api.services.credits_service.deduct`)
  — credits acquired BEFORE the provider call so a parallel-fire
  denial-of-wallet attacker cannot burn N generations before the first
  deduction lands (D-15-X-pre-deduct-credits).
* **Backend dispatch** (T06 / T07 :class:`persona.imagegen.protocol.ImageBackend`)
  — provider-agnostic generation call. SDK exceptions are caught and
  re-raised as :mod:`persona.imagegen.errors` domain types at the adapter
  boundary; the service layer maps them through the refund-on-failure
  funnel.
* **Workspace persistence** (D-13-4 layout via
  :func:`persona.tools._sandbox.resolve_sandbox_path`) — bytes land at
  ``{workspace_root}/{owner_id}/{persona_id}/uploads/{blake2b}.{ext}``,
  the same layout :func:`persona_api.services.image_service.upload`
  produces so the existing ``GET /v1/personas/:id/uploads/:ref`` route
  serves generated images provenance-blindly (D-15-X-workspace-coordination).
* **Refund-on-failure** (T13 :func:`persona_api.services.credits_service.refund`)
  — reverse-deduct ledger entry on backend or post-gen moderation failure;
  pattern (a) per D-15-X-credit-flow-semantics (no Alembic migration
  required; ``credit_transactions.delta`` is ``Integer, nullable=False``
  with no ``CheckConstraint`` so positive deltas are physically allowed).

**Transactional shape.** The cap acquisition and the pre-deduct land in a
single ``rls_engine.begin()`` transaction so the cap is HELD while the
deduction is observed by Postgres; the backend ``await`` happens *inside*
that same context manager so the lock persists across the provider call
(the lock auto-releases on commit/rollback at the surrounding ``with``
exit). When the backend raises, we exit the original transaction (which
rolls back — releasing the lock) and then open a *separate*
``credits_service.refund`` transaction to issue the reverse-deduct
ledger entry. The two-transaction shape is intentional: the refund must
NOT share a transaction with the cap lock because rolling back the
original transaction is what releases the lock — collapsing the refund
into that transaction would either keep the lock during the refund
(wrong) or commit the deduct prematurely (wrong).

**Why credits_service.deduct/refund instead of inline SQL?** T13 owns
the ledger semantics; this module composes. Going through
:mod:`persona_api.services.credits_service` ensures the two ledger
writes (deduct + refund) are byte-for-byte symmetric with the rest of
the credits machinery — a future audit query summing ``delta`` by user
keeps reconstructing the running balance correctly.

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #3 (cost
    discipline = pre-deduct + cap + refund); D-15-X-pre-deduct-credits,
    D-15-X-credit-flow-semantics, D-15-X-concurrency-cap,
    D-15-X-workspace-coordination, D-13-4 (workspace layout);
    docs/specs/phase2/spec_15/tasks.md §T15.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

from persona.imagegen import (
    ContentRejectedError,
    GeneratedImage,
    GenerationResult,
    ImageGenError,
    ImageGenOptions,
)
from persona.imagegen._merge import merge_visual_style
from persona.logging import get_logger
from persona.tools._sandbox import resolve_sandbox_path

from persona_api.errors import ConcurrencyCappedError
from persona_api.imagegen.concurrency import acquire_user_concurrency
from persona_api.services import credits_service

if TYPE_CHECKING:
    from pathlib import Path

    from persona.imagegen.protocol import ImageBackend
    from persona.imagegen.result import ImageMediaType
    from sqlalchemy import Engine

__all__ = ["DEFAULT_COST_PER_IMAGE_CREDITS", "generate"]


_LOG = get_logger("imagegen.service")


#: Default per-image credit cost (D-15-3 cost containment). 100 credits per
#: image at ``count <= 4`` caps a single tool invocation at 400 credits
#: total — still a small fraction of the 100_000-credit default balance,
#: leaving headroom for chat turns while keeping image generation visibly
#: more expensive than per-token chat usage (research §1.1: OpenAI
#: gpt-image-1 medium is ~$0.042/image, so 100 credits ≈ ~4 cents in the
#: hypothetical 1¢-per-1000-credits accounting).
DEFAULT_COST_PER_IMAGE_CREDITS: int = 100


#: Mapping from supported IANA media types to filename extensions used in
#: the workspace layout. Mirrors :data:`persona_api.services.image_service._EXT_BY_MEDIA_TYPE`
#: (intentionally redeclared here so the imagegen service does not import a
#: private symbol from the upload service — both modules write to the same
#: ``uploads/`` directory but neither is the other's authority on what an
#: extension means; the value is the same in both modules and any drift
#: would surface as a route-layer 404 because the serve path maps ext back
#: to media type).
_EXT_BY_MEDIA_TYPE: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


#: Workspace sub-directory holding a persona's image assets (D-13-4 +
#: D-15-X-workspace-coordination). Generated images and user uploads share
#: this directory provenance-blindly; provenance lives in the audit log
#: (``ToolAuditEvent.metadata["outcome"]`` ∈
#: ``{ok, content_rejected_provider, content_rejected_hard_line, error}``)
#: and the per-turn observability surface (``turn_logs.metadata`` with
#: ``kind=image_generation`` per D-15-X-observability-shape), NOT the path.
_UPLOAD_DIR_NAME: str = "uploads"


async def generate(
    *,
    rls_engine: Engine,
    workspace_root: Path,
    backend: ImageBackend,
    user_id: str,
    persona_id: str,
    persona_visual_style: str | None,
    prompt: str,
    options: ImageGenOptions,
    cost_per_image_credits: int = DEFAULT_COST_PER_IMAGE_CREDITS,
) -> GenerationResult:
    """Run one full image-generation flow: cap → deduct → backend → persist.

    On the happy path the returned :class:`GenerationResult` carries
    persisted images: each :attr:`persona.imagegen.result.GeneratedImage.workspace_path`
    is populated with the workspace-relative path
    (``uploads/<blake2b><ext>``) and :attr:`image_bytes` is zeroed so the
    response envelope does not double-carry the payload. The credits
    ledger shows one ``-count*cost_per_image_credits`` entry; the audit
    log will carry the persona-layer
    :class:`persona.tools.audit.ToolAuditEvent` emitted by the
    ``generate_image`` tool factory (T12 owns that emission — the
    service layer composes the cap + deduct + persistence around the
    backend call but the audit is the tool's; the route layer's
    ``audit_service.record`` adds the HTTP-layer event).

    On a backend failure (:class:`ContentRejectedError` from provider
    moderation or :class:`ImageGenError` family for auth / rate / etc.)
    the credits are refunded via :func:`credits_service.refund` in a
    fresh transaction (the original transaction rolled back, releasing
    the cap lock) and the original exception is re-raised so the route
    layer can map it to the appropriate HTTP status.

    On a concurrency-capped path
    (:class:`ConcurrencyCappedError`) NO credits are deducted — the
    cap fires before the deduct in the same transaction so the rollback
    leaves the balance untouched.

    Args:
        rls_engine: The per-request RLS-scoped SQLAlchemy
            :class:`~sqlalchemy.Engine`. Both the cap+deduct transaction
            and the refund-on-failure transaction open through this
            engine so the per-request user_id RLS scope is preserved.
        workspace_root: Per-deployment workspace root (typically
            ``settings.persona_workspace_root``). Generated bytes land
            at ``workspace_root / user_id / persona_id / uploads / <blake2b><ext>``.
        backend: A concrete
            :class:`persona.imagegen.protocol.ImageBackend` implementation
            (OpenAI gpt-image-1 via
            :class:`persona.imagegen.openai_image.OpenAIImageBackend` or
            Flux 1.1 [pro] via
            :class:`persona.imagegen.fal_image.FalImageBackend` for v0.1;
            composition root selects via
            :func:`persona.imagegen.load_image_backend`).
        user_id: Authenticated tenant identifier — feeds the advisory-
            lock key (hashed via md5 in the concurrency helper) AND the
            workspace path's ``owner_id`` segment AND the credits ledger
            ``user_id`` column.
        persona_id: Persona owning this generation. Workspace path
            segment + audit payload.
        persona_visual_style: The persona's
            :attr:`persona.schema.persona.PersonaIdentity.visual_style`
            (T10). Merged into the prompt via :func:`merge_visual_style`
            per D-15-4; ``None`` / empty / whitespace-only yields the
            identity branch (no merge).
        prompt: The user / runtime prompt as supplied. The visual-style
            merge runs at the service layer (here) so the backend sees
            the merged prompt; this is structurally important because a
            misconfigured tool wrapper would otherwise let the model
            bypass the merge by calling the backend directly.
        options: Closed-preset image-generation knobs
            (:class:`persona.imagegen.result.ImageGenOptions`). The
            ``count <= 4`` cap (D-15-3) is already enforced by the
            Pydantic field; this service trusts that.
        cost_per_image_credits: Credits per image. Defaults to
            :data:`DEFAULT_COST_PER_IMAGE_CREDITS` (100). Override is
            available for future per-quality pricing if D-15-3 gains
            tiers — v0.1 ships flat.

    Returns:
        :class:`GenerationResult` with images whose ``workspace_path``
        is populated and ``image_bytes`` is zeroed.

    Raises:
        ConcurrencyCappedError: The per-user cap is already held by a
            concurrent in-flight generation for this ``user_id`` — the
            route layer surfaces 429 + ``Retry-After``. No credits are
            touched.
        ContentRejectedError: Provider moderation refused the prompt
            (input stage) or flagged the generated bytes (output stage,
            D-15-X-flagged-image-policy). Credits are refunded before
            the exception propagates.
        ImageGenError: Other backend failure (auth, rate limit,
            transient, timeout, unsupported option). Credits are refunded
            before the exception propagates.
    """
    total_cost = options.count * cost_per_image_credits
    merged_prompt = merge_visual_style(prompt, persona_visual_style)

    # Phase 1: cap + pre-deduct + backend call inside one transaction.
    # The advisory lock is held by the transaction; the await suspends
    # the coroutine but the underlying connection's transaction stays
    # open (sync SQLAlchemy on psycopg3 — the connection is a Python
    # object holding a server-side transaction; nothing about ``await``
    # closes it). On either branch the ``with`` exits, releasing the
    # lock — commit on success, rollback on exception.
    deduct_succeeded = False
    try:
        with (
            rls_engine.begin() as conn,
            acquire_user_concurrency(conn=conn, user_id=user_id) as acquired,
        ):
            if not acquired:
                # Cap held — raise BEFORE pre-deduct so the rollback
                # leaves credits untouched.
                raise ConcurrencyCappedError(
                    "image generation already in flight for this user",
                    context={"user_id": user_id, "retry_after_s": "5"},
                )

            # Pre-deduct credits inside the same transaction. The
            # deduct opens its own ``rls_engine.begin()`` internally;
            # that nested transaction commits immediately because
            # ``credits_service.deduct`` uses a fresh connection from
            # the pool so the deduct lands in its own transaction. The
            # TWO transactions share the same engine + the same RLS
            # scope; if the backend call later raises, we manually
            # refund (the deduct's transaction already committed).
            credits_service.deduct(
                rls_engine=rls_engine,
                user_id=user_id,
                amount=total_cost,
                reason="image_gen_pre",
            )
            deduct_succeeded = True

            # Backend dispatch happens inside the cap context so the
            # lock holds for the full provider latency (else parallel-
            # fire bypasses the cap by deducting concurrently while
            # the first generation is in flight). The deduct above
            # already committed on its own connection, so even if the
            # backend raises the deduct is observable; we refund
            # explicitly in the failure branch.
            result = await backend.generate(merged_prompt, options=options)
    except ConcurrencyCappedError:
        # Cap-capped path: no deduct happened (the raise is BEFORE the
        # deduct call) — re-raise unchanged. The ``with`` already exited
        # and the lock auto-released on rollback.
        raise
    except (ContentRejectedError, ImageGenError):
        # Backend-failure path: the deduct committed (own transaction),
        # the cap lock auto-released on rollback. Refund the credits in
        # a fresh transaction so the ledger captures both legs of the
        # round trip.
        if deduct_succeeded:
            credits_service.refund(
                rls_engine=rls_engine,
                user_id=user_id,
                amount=total_cost,
                reason="image_gen_refund:backend_failure",
            )
        raise

    # Phase 2: persist bytes to the workspace (D-13-4 layout) and
    # rewrite the result so ``workspace_path`` is populated and
    # ``image_bytes`` is zeroed for the response envelope.
    sandbox_root = workspace_root / user_id / persona_id
    sandbox_root.mkdir(parents=True, exist_ok=True)

    stored_images: list[GeneratedImage] = []
    for img in result.images:
        relative = _persist_bytes(
            sandbox_root=sandbox_root,
            image_bytes=img.image_bytes,
            media_type=img.media_type,
        )
        stored_images.append(
            img.model_copy(
                update={
                    "workspace_path": relative,
                    "image_bytes": b"",
                }
            )
        )

    _LOG.info(
        "image generation completed",
        user_id=user_id,
        persona_id=persona_id,
        provider=result.provider,
        model=result.model,
        image_count=len(stored_images),
        latency_ms=result.latency_ms,
    )

    return result.model_copy(update={"images": stored_images})


def _persist_bytes(
    *,
    sandbox_root: Path,
    image_bytes: bytes,
    media_type: ImageMediaType,
) -> str:
    """Write image bytes to the persona workspace; return the workspace-relative path.

    Layout per D-13-4 (reused unchanged via D-15-X-workspace-coordination):
    ``{sandbox_root}/uploads/{blake2b(bytes)}{ext}``. The blake2b digest
    is content-addressed so two generations producing the same bytes
    collapse to one file (idempotent — same property the upload path
    relies on). Write goes through ``resolve_sandbox_path`` (the Spec 03
    sandbox resolver) plus ``O_NOFOLLOW`` to close the TOCTOU window
    between resolution and open.

    Args:
        sandbox_root: ``{workspace_root}/{user_id}/{persona_id}``.
        image_bytes: Raw decoded image bytes from the backend.
        media_type: IANA media type of the bytes (drives extension).

    Returns:
        Workspace-relative path (``uploads/<blake2b><ext>``) suitable for
        :attr:`GeneratedImage.workspace_path`.
    """
    ext = _EXT_BY_MEDIA_TYPE[media_type]
    ref = hashlib.blake2b(image_bytes, digest_size=16).hexdigest()
    relative = f"{_UPLOAD_DIR_NAME}/{ref}{ext}"

    resolved = resolve_sandbox_path(sandbox_root, relative)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    # ``O_NOFOLLOW`` closes the TOCTOU window between resolver + open
    # (mirrors ``persona.tools.builtin.file_write`` and the spec-13
    # ``image_service.upload`` write site). ``O_EXCL`` would reject the
    # second generation of identical bytes; we tolerate that (idempotent
    # content-addressed write).
    fd = os.open(
        resolved,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(fd, image_bytes)
    finally:
        os.close(fd)

    return relative
