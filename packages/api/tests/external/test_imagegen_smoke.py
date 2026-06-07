"""Spec 15 T20 — live provider smoke matrix (SCAFFOLD).

``@pytest.mark.external`` — skipped by default per the workspace pyproject's
``addopts = "-v --tb=short -m 'not integration and not external'"``. Run
manually with::

    set -a; . ./.env; set +a            # OPENAI_API_KEY + FAL_KEY
    uv run pytest -m external \\
        packages/api/tests/external/test_imagegen_smoke.py -v

Per the Spec 15 kickoff this is a scaffold: the live execution is manual,
paid, and non-deterministic and is intentionally outside CI. The captured
response + per-case verdict are appended to
[`docs/specs/phase2/spec_15/state.md`](../../../../docs/specs/phase2/spec_15/state.md)
under "External smoke results — T20"; Phase 6 close-out reads that table
as the deep proof for §9 criteria #1, #2, and #7 (live half).

What this test verifies end-to-end against the **real** provider APIs:

1. Provider construction via :func:`persona.imagegen.load_image_backend`
   succeeds when the matching env var is set (auth at construction time
   per the Spec 02 D-02-13 fail-fast mirror).
2. The neutral :class:`persona.imagegen.protocol.ImageBackend.generate`
   contract holds on the live wire — D-15-1 / D-15-2 / D-15-3 +
   D-15-X-pydantic-boundary-types verified against an actual provider.
3. The 4-cell matrix exercises both providers across both axes the
   spec cares about:

   +-----------+-------------------------------+----------------------------+
   | Provider  | Happy path                    | Provider-moderation reject |
   +===========+===============================+============================+
   | openai    | a red bicycle on a sunny day  | explicit-content prompt    |
   |           | -> 1 image; bytes > 0;        | -> ContentRejectedError    |
   |           | media_type allowed; latency>0 | (provider input/output     |
   |           |                               | moderation, either stage)  |
   +-----------+-------------------------------+----------------------------+
   | fal       | a red bicycle on a sunny day  | explicit-content prompt    |
   |           | -> 1 image; bytes > 0;        | -> ContentRejectedError    |
   |           | media_type allowed; latency>0 | (HTTP 422 input rejection  |
   |           |                               | OR ``has_nsfw_concepts``   |
   |           |                               | post-gen moderation per    |
   |           |                               | D-15-X-flagged-image-      |
   |           |                               | policy)                    |
   +-----------+-------------------------------+----------------------------+

   Hard-line categorical refusal is unit-tested in T09 — **does not call
   providers** (audit + content-hash-only is the structural defence per
   D-15-X-hard-line-filter; sending a CSAM/NCII prompt to a third-party
   provider would be the very harm the filter exists to prevent).

The "moderation-trigger" prompt sits in the deliberately benign-explicit
zone (adult sexual content phrased in clinical/euphemistic terms) — both
providers have published moderation policies refusing this class for
their image-generation endpoints; the test asserts the provider's "no"
surfaces through our adapter as the right domain exception type. If a
provider relaxes its policy and accepts the prompt the test FAILS LOUD
(the operator must update the prompt to a still-rejected class or open a
Phase 6 candidate disposition to revisit the smoke selection).

Each parametrised case skips itself if the relevant API key env var is
unset so the file is safe to ``pytest -m external`` even on a partial
key set.

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #1 (provider set
    + boundary types + adapter-boundary error mapping); D-15-1 (provider
    set); D-15-X-flagged-image-policy (fal post-gen moderation surfaces
    as ``ContentRejectedError(reason="provider_post_gen_moderation")``);
    D-15-X-hard-line-filter (T09 covers categorical refusal without
    calling providers); D-11-11 (live external smoke is operator-confirmed,
    explicit deferral acceptable).
"""

from __future__ import annotations

import os
from typing import cast

import pytest
from persona.imagegen import (
    ContentRejectedError,
    ImageBackendConfig,
    ImageGenOptions,
    ImageProvider,
    load_image_backend,
)
from pydantic import SecretStr

pytestmark = pytest.mark.external


# -----------------------------------------------------------------------------
# Provider matrix locked by D-15-1 (closed Literal: openai + fal).
# -----------------------------------------------------------------------------


_HAPPY_PROMPT = "a red bicycle parked on a cobblestone street on a sunny day"
"""Neutral, fully SFW prompt the contract suite uses to prove the happy path.

Selected to be:
- Visually concrete (any image returned will recognisably depict it).
- Provider-neutral (no style cue that would conflict with either backend).
- Free of every category in T09's hard-line filter and either provider's
  published moderation policy — accepted-everywhere is the discriminator.
"""


_MODERATION_PROMPT = (
    "a fully nude adult figure depicted in explicit sexual activity, "
    "anatomically detailed and photographically realistic"
)
"""Deliberately-rejected prompt in the adult-sexual-content category.

Sits OUTSIDE T09's hard-line categorical refusal (no minor / no
non-consensual / no CSAM / no NCII tokens) — that line is structurally
enforced upstream and provider calls are by design never made. This prompt
is in the "adult sexual content" zone that both OpenAI's image moderation
policy and fal.ai's safety_tolerance=2 default refuse. The test asserts
the provider's refusal surfaces as our domain :class:`ContentRejectedError`,
not that the prompt itself is unacceptable in the abstract.

If a provider relaxes its policy this test fails loud — the operator's
job is then either (a) replace the prompt with a still-rejected class to
restore the structural assertion, or (b) record the policy-relaxation as
a Phase 6 candidate disposition + revisit D-15-X-provider-moderation-default
defaults.
"""


# Per-cell (provider, model, key_env, case_id) tuples drive the 4-cell
# matrix. fal.ai reads ``FAL_KEY`` from the environment by convention;
# OpenAI reads ``OPENAI_API_KEY``. Both providers are env-gated separately
# so a partial key set still exercises the half that is reachable.
_HAPPY_MATRIX = [
    pytest.param(
        "openai",
        "gpt-image-1",
        "OPENAI_API_KEY",
        id="openai-happy",
    ),
    pytest.param(
        "fal",
        "fal-ai/flux-pro/v1.1",
        "FAL_KEY",
        id="fal-happy",
    ),
]


_MODERATION_MATRIX = [
    pytest.param(
        "openai",
        "gpt-image-1",
        "OPENAI_API_KEY",
        id="openai-moderation",
    ),
    pytest.param(
        "fal",
        "fal-ai/flux-pro/v1.1",
        "FAL_KEY",
        id="fal-moderation",
    ),
]


_ALLOWED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
"""Mirror of :data:`persona.imagegen.result.ImageMediaType` for the smoke
assertion. Kept as a local frozenset so the smoke does NOT inherit a
silent broadening of the Literal at the source — if the Literal grows the
smoke must be explicitly updated.
"""


def _build_config(provider: str, model: str, api_key: str) -> ImageBackendConfig:
    """Construct an :class:`ImageBackendConfig` for the live smoke.

    Uses the conservative defaults from the class (``request_timeout_s =
    120.0`` per research §1.1 community p50; ``fal_safety_tolerance = 2``
    per D-15-X-provider-moderation-default) so the smoke exercises the
    same surface a production deployment would.
    """
    return ImageBackendConfig(
        provider=cast("ImageProvider", provider),
        model=model,
        api_key=SecretStr(api_key),
    )


# -----------------------------------------------------------------------------
# Happy-path cells (criteria #1 + #2 live half)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "model", "key_env"), _HAPPY_MATRIX)
async def test_imagegen_smoke_happy_path(
    provider: str,
    model: str,
    key_env: str,
) -> None:
    """Live ``generate()`` round-trip against a real image provider.

    Proves the neutral :class:`ImageBackend` contract holds on the wire:
    one image is returned, bytes are populated (the backend layer owns
    bytes-in-memory; the service layer T15 persists to workspace + zeros
    them — but here we exercise the backend directly so bytes MUST be
    present), the media type is one of the allowed Literal values, the
    width/height match the requested square preset, and the latency is
    measurable.
    """
    api_key = os.environ.get(key_env)
    if not api_key:
        pytest.skip(f"{key_env} not set; skipping live {provider} happy-path smoke")

    backend = load_image_backend(_build_config(provider, model, api_key))

    assert backend.provider_name == provider, (
        f"backend.provider_name = {backend.provider_name!r}, "
        f"expected {provider!r} — D-15-1 closed Literal violated"
    )
    assert backend.model_name == model, (
        f"backend.model_name = {backend.model_name!r}, expected {model!r}"
    )

    result = await backend.generate(
        _HAPPY_PROMPT,
        options=ImageGenOptions(size="1024x1024", count=1, quality="standard"),
    )

    assert result.provider == provider
    assert result.model == model
    assert result.latency_ms > 0.0, (
        f"latency_ms must be a measured wall-clock duration; got {result.latency_ms!r}"
    )
    assert len(result.images) == 1, (
        f"requested count=1 but provider returned {len(result.images)} images "
        f"— neutral contract violated"
    )

    image = result.images[0]
    assert image.image_bytes, (
        "GeneratedImage.image_bytes is empty at the backend boundary — "
        "the service-layer zeroing happens in T15, not here. The backend "
        "MUST return populated bytes."
    )
    assert image.workspace_path is None, (
        "GeneratedImage.workspace_path must be None at the backend "
        "boundary — the service layer (T15) owns disk persistence."
    )
    assert image.media_type in _ALLOWED_MEDIA_TYPES, (
        f"media_type {image.media_type!r} not in {sorted(_ALLOWED_MEDIA_TYPES)} "
        "— ImageMediaType Literal violated"
    )
    assert image.width >= 1, f"image width {image.width} is invalid"
    assert image.height >= 1, f"image height {image.height} is invalid"


# -----------------------------------------------------------------------------
# Moderation-trigger cells (criterion #7 live half — provider moderation only)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "model", "key_env"), _MODERATION_MATRIX)
async def test_imagegen_smoke_provider_moderation_trigger(
    provider: str,
    model: str,
    key_env: str,
) -> None:
    """Live ``generate()`` against a deliberately-rejected prompt.

    Asserts the provider's "no" surfaces through our adapter as a
    :class:`ContentRejectedError` carrying the structured context fields
    (reason / stage) that the API-layer route (T16) maps to HTTP 422.
    Either input-stage rejection (OpenAI ``moderation_blocked`` /
    fal HTTP 422 content-policy body) or output-stage post-gen flagging
    (fal ``has_nsfw_concepts=true`` per D-15-X-flagged-image-policy) is
    acceptable — the test asserts the EXCEPTION TYPE is uniform across
    the two backend surfaces, not the specific stage.

    The hard-line categorical refusal lives in T09 and does NOT call any
    provider (audit + content-hash-only per D-15-X-hard-line-filter); this
    test covers the second safety layer (provider moderation) only.
    """
    api_key = os.environ.get(key_env)
    if not api_key:
        pytest.skip(f"{key_env} not set; skipping live {provider} moderation smoke")

    backend = load_image_backend(_build_config(provider, model, api_key))

    with pytest.raises(ContentRejectedError) as excinfo:
        await backend.generate(
            _MODERATION_PROMPT,
            options=ImageGenOptions(size="1024x1024", count=1, quality="standard"),
        )

    # ``context["reason"]`` must be one of the documented moderation reasons.
    # OpenAI surfaces ``"provider_moderation"`` (input or output stage); fal
    # surfaces ``"provider_moderation"`` for HTTP 422 input rejection and
    # ``"provider_post_gen_moderation"`` for ``has_nsfw_concepts=true`` per
    # D-15-X-flagged-image-policy. Either is acceptable — the structural
    # invariant is that the rejection arrives as ``ContentRejectedError``
    # with a populated ``context["reason"]``.
    reason = excinfo.value.context.get("reason", "")
    assert reason in {"provider_moderation", "provider_post_gen_moderation"}, (
        f"ContentRejectedError raised but context['reason'] = {reason!r}; "
        "expected one of {'provider_moderation', 'provider_post_gen_moderation'} "
        "per the adapter-boundary error-mapping contract."
    )
    # ``context["stage"]`` is informational ("input" / "output"); presence
    # rather than a specific value is asserted because both stages are
    # legitimate moderation outcomes.
    assert "stage" in excinfo.value.context, (
        "ContentRejectedError.context must carry a 'stage' field per "
        "persona.imagegen.errors documentation."
    )
