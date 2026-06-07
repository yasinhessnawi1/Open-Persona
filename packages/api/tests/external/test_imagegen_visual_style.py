"""Spec 15 T19 — visual_style empirical merge external smoke (SCAFFOLD).

``@pytest.mark.external`` — skipped by default per the workspace
pyproject's ``addopts = "-v --tb=short -m 'not integration and not
external'"``. Each parametrised case additionally self-skips if
``OPENAI_API_KEY`` AND ``FAL_KEY`` are not both set in the environment
(per the T19 kickoff: the suite intentionally exercises both providers
so the visual-style merge is verified across the D-15-1 alternative
matrix). Run manually with::

    OPENAI_API_KEY=sk-... FAL_KEY=fal-... \\
        uv run pytest -m external \\
        packages/api/tests/external/test_imagegen_visual_style.py

What this smoke verifies (the two structural halves of acceptance
criterion #6):

* **Pipe-runs (pytest layer):** the merged prompt produced by
  :func:`persona.imagegen.merge_visual_style` reaches the live backend,
  the backend returns at least one ``GeneratedImage`` with non-empty
  bytes, and the bytes decode to a valid PNG / JPEG magic header. This
  is the deterministic structural assertion the harness can make
  without a model-in-the-loop visual judge.
* **Visual judgement (operator layer):** the eight parametrised cases
  from research.md §3.5 are operator-passed — the bytes land at the
  tmp_path the test writes them to, the operator opens each image and
  records a 🟦 / 🟧 verdict in ``state.md``'s "External smoke results"
  table under the "T19 visual_style empirical" sub-heading. The
  pytest layer never auto-passes the visual half.

The eight cases (research.md §3.5):

1. ``warm editorial illustration, muted earth palette`` + ``a cat``
   → recognisable cat, warm/editorial aesthetic.
2. ``cool blue tones, minimalist line art`` + ``a cat``
   → recognisable cat, cool/minimalist aesthetic.
3. ``hand-drawn illustration`` + ``a coffee cup``
   → drawn, not photographed.
4. ``photorealistic, shallow depth of field`` + ``a coffee cup``
   → photographed, not drawn.
5. ``abstract geometric, primary colours`` + ``a tree``
   → recognisable tree, abstract treatment.
6. **Conflict case (criterion #6):**
   ``dark moody, low-key lighting`` + ``a cheerful birthday card``
   → cheerful birthday card wins; perhaps muted palette but content
   unmistakable.
7. **Explicit-user-style override:** ``dark moody`` +
   ``a cat in the style of Van Gogh``
   → Van Gogh cat; persona style not applied (the merge function
   short-circuits to identity via heuristic 1 — substring
   ``"in the style of"`` is present in the user prompt).
8. **Non-English style descriptor:** ``akvarell, dempete farger``
   (Norwegian) + ``a cat`` → recognisable cat, watercolour aesthetic.

Each case is parametrised across both v0.1 providers (OpenAI
``gpt-image-1`` per D-15-X-demo-primary-provider; fal Flux 1.1 [pro]
via ``fal-ai/flux-pro/v1.1`` per D-15-1) so the merge is verified
against the full D-15-1 surface — 8 cases × 2 providers = 16
parametrised runs when both keys are present.

Operator capture pattern borrowed from Spec 13 fold-in #9: the test
writes a manifest line per case (``case_id``, ``provider``,
``merged_prompt``, ``output_path``, ``media_type``, ``width``,
``height``, ``revised_prompt``) to ``tmp_path / "manifest.jsonl"`` so
the operator can grep the artifacts and walk the cases in order.

References:
    docs/specs/phase2/spec_15/tasks.md §T19;
    docs/specs/phase2/spec_15/research.md §3.5;
    docs/specs/phase2/spec_15/decisions.md D-15-1 + D-15-4 +
    D-15-X-demo-primary-provider.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from persona.imagegen import (
    ImageBackendConfig,
    ImageGenOptions,
    load_image_backend,
)
from persona.imagegen._merge import merge_visual_style
from pydantic import SecretStr

if TYPE_CHECKING:
    from persona.imagegen.config import ImageProvider
    from persona.imagegen.protocol import ImageBackend

pytestmark = pytest.mark.external


# -----------------------------------------------------------------------------
# Image-format magic-byte sniffing — the pytest-layer structural assertion.
# -----------------------------------------------------------------------------
#
# The smoke does not depend on Pillow: a four-byte prefix check is enough
# to prove that what the backend handed back is a real image, not an
# error envelope mis-decoded as bytes. The two recognised formats cover
# every value of ``ImageMediaType`` the v0.1 providers emit (PNG from
# OpenAI ``b64_json``; JPEG / PNG from fal depending on the model
# variant).

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
"""PNG file signature — RFC 2083 §3.1."""

_JPEG_SOI = b"\xff\xd8\xff"
"""JPEG start-of-image marker — first three bytes of every JFIF / EXIF
JPEG. The fourth byte discriminates JFIF (``\\xe0``) from EXIF
(``\\xe1``) but we don't need that level of detail to prove validity.
"""


def _looks_like_png_or_jpeg(payload: bytes) -> tuple[bool, str]:
    """Return ``(is_valid, format_hint)`` for the given byte payload.

    ``format_hint`` is one of ``"png"`` / ``"jpeg"`` / ``"unknown"`` and
    is used in assertion messages to help the operator localise mismatches
    (e.g. provider returned base64 of an HTML error page).
    """
    if payload.startswith(_PNG_MAGIC):
        return True, "png"
    if payload.startswith(_JPEG_SOI):
        return True, "jpeg"
    return False, "unknown"


# -----------------------------------------------------------------------------
# Provider matrix locked by D-15-1 + D-15-X-demo-primary-provider.
# -----------------------------------------------------------------------------

_PROVIDERS = [
    pytest.param(
        "openai",
        "gpt-image-1",
        "OPENAI_API_KEY",
        id="openai-gpt-image-1",
    ),
    pytest.param(
        "fal",
        "fal-ai/flux-pro/v1.1",
        "FAL_KEY",
        id="fal-flux-pro-v1-1",
    ),
]


# -----------------------------------------------------------------------------
# The eight parametrised cases per research.md §3.5.
# -----------------------------------------------------------------------------
#
# Each tuple is (case_id, visual_style, prompt, expected_behaviour). The
# ``expected_behaviour`` field is the prose the operator scores against
# in state.md's "External smoke results" table — the pytest layer NEVER
# auto-judges it.

_VISUAL_STYLE_CASES = [
    pytest.param(
        "warm-style-cat",
        "warm editorial illustration, muted earth palette",
        "a cat",
        "recognisable cat, warm/editorial aesthetic",
        id="warm-style-cat",
    ),
    pytest.param(
        "cool-style-cat",
        "cool blue tones, minimalist line art",
        "a cat",
        "recognisable cat, cool/minimalist aesthetic",
        id="cool-style-cat",
    ),
    pytest.param(
        "illustration-coffee-cup",
        "hand-drawn illustration",
        "a coffee cup",
        "drawn, not photographed",
        id="illustration-coffee-cup",
    ),
    pytest.param(
        "photo-coffee-cup",
        "photorealistic, shallow depth of field",
        "a coffee cup",
        "photographed, not drawn",
        id="photo-coffee-cup",
    ),
    pytest.param(
        "abstract-tree",
        "abstract geometric, primary colours",
        "a tree",
        "recognisable tree, abstract treatment",
        id="abstract-tree",
    ),
    pytest.param(
        "conflict-birthday-dark",
        "dark moody, low-key lighting",
        "a cheerful birthday card",
        (
            "cheerful birthday card wins; perhaps muted palette but "
            "content unmistakable (criterion #6 conflict resolution)"
        ),
        id="conflict-birthday-dark",
    ),
    pytest.param(
        "explicit-user-override-vangogh",
        "dark moody",
        "a cat in the style of Van Gogh",
        (
            "Van Gogh cat — persona style not applied (merge function "
            "short-circuits to identity via heuristic 1)"
        ),
        id="explicit-user-override-vangogh",
    ),
    pytest.param(
        "non-english-watercolour",
        "akvarell, dempete farger",
        "a cat",
        "recognisable cat, watercolour aesthetic (Norwegian descriptor)",
        id="non-english-watercolour",
    ),
]


# -----------------------------------------------------------------------------
# Env-gating helper — both keys must be present for the suite to run.
# -----------------------------------------------------------------------------


def _both_keys_present() -> bool:
    """Return True iff BOTH ``OPENAI_API_KEY`` and ``FAL_KEY`` are set.

    Per the T19 kickoff: the suite intentionally exercises both
    providers; a partial key set short-circuits the entire suite to
    SKIP. The per-case ``key_env`` parameter is then the second-layer
    fail-safe — if a key disappears mid-run the matching case still
    skips cleanly rather than 401-ing through paid API spend.
    """
    return bool(os.environ.get("OPENAI_API_KEY")) and bool(os.environ.get("FAL_KEY"))


def _extension_for_media_type(media_type: str) -> str:
    """Return the canonical file extension for an ``ImageMediaType``."""
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(media_type, ".bin")


def _build_backend(provider: str, model: str, api_key: str) -> ImageBackend:
    """Construct a real :class:`ImageBackend` for the given provider.

    Mirrors the startup-composition pattern used by
    :func:`persona_api.app.lifespan` — :func:`load_image_backend` with
    an :class:`ImageBackendConfig` populated explicitly so the test does
    not pick up arbitrary ``PERSONA_IMAGEGEN_*`` env vars from the
    operator's shell.
    """
    config = ImageBackendConfig(
        # ``provider`` is parametrised against the closed ``ImageProvider``
        # Literal — the cast narrows the parametrize-string back to the
        # Literal so the Pydantic v2 field validator accepts it without
        # surrendering type strictness elsewhere in the file.
        provider=cast("ImageProvider", provider),
        model=model,
        api_key=SecretStr(api_key),
        request_timeout_s=180.0,  # generous for live image-gen; OpenAI p99 > 60s
    )
    return load_image_backend(config)


# -----------------------------------------------------------------------------
# Suite-level skip when both keys are not present.
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _skip_if_either_key_missing() -> None:
    """Skip the entire suite if either of the two required keys is unset.

    The autouse fixture fires per-case; if a key disappears mid-suite
    (e.g. the operator unsets ``FAL_KEY`` between two runs) the suite
    remains skip-coherent.
    """
    if not _both_keys_present():
        pytest.skip(
            "T19 visual_style empirical smoke requires BOTH OPENAI_API_KEY "
            "and FAL_KEY in the environment (per the Spec 15 D-15-1 "
            "provider matrix). Skipping the full suite."
        )


# -----------------------------------------------------------------------------
# Per-case live round-trip — the headline T19 test.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "model", "key_env"), _PROVIDERS)
@pytest.mark.parametrize(
    ("case_id", "visual_style", "prompt", "expected_behaviour"),
    _VISUAL_STYLE_CASES,
)
async def test_visual_style_live_merge(  # noqa: PLR0913 - parametrize fan-out
    provider: str,
    model: str,
    key_env: str,
    case_id: str,
    visual_style: str,
    prompt: str,
    expected_behaviour: str,
    tmp_path: Path,
) -> None:
    """Live round-trip per (case, provider) pair; structural assertion only.

    Steps:

    1. Compose the merged prompt via
       :func:`persona.imagegen.merge_visual_style` — exercises D-15-4
       deterministic mechanics against each live case so the
       operator-facing artifact carries the exact string the live model
       saw.
    2. Construct an :class:`ImageBackend` for the parametrised provider
       via :func:`load_image_backend`.
    3. Call ``await backend.generate(merged_prompt, options=opts)``
       with ``count=1`` to bound cost (D-15-3 cap; T19 uses the floor
       not the cap to keep operator spend minimal).
    4. Pytest-layer assertions:

       * the returned ``GenerationResult`` carries ``provider`` and
         ``model`` matching the parametrisation;
       * exactly one ``GeneratedImage`` with non-empty ``image_bytes``;
       * the bytes start with a PNG or JPEG magic-byte prefix;
       * the recorded ``media_type`` is one of the three values in
         :data:`ImageMediaType`;
       * ``width >= 1024`` and ``height >= 1024`` (sanity check
         against truncated downloads).

    5. Write the bytes to ``tmp_path / f"{provider}_{case_id}.<ext>"``
       and append a JSON-lines manifest entry to
       ``tmp_path / "manifest.jsonl"`` so the operator can walk the
       artifacts in order when filling out the state.md verdict
       table.

    The pytest layer does NOT judge the visual outcome. The operator
    opens each ``<provider>_<case_id>.<ext>`` file under the test's
    ``tmp_path`` (printed at the end of the test via ``print``-on-fail)
    and records the 🟦 PASS / 🟧 FAIL verdict + observed prose in
    state.md.
    """
    api_key = os.environ.get(key_env)
    if not api_key:  # pragma: no cover - guarded by the autouse fixture
        pytest.skip(f"{key_env} not set; per-case fail-safe skip for {provider}")

    merged_prompt = merge_visual_style(prompt, visual_style)
    # Two structural sanity checks against the merge function itself
    # before any paid call — the merge module's deterministic unit
    # tests live in core, but mirroring them here closes the loop
    # between the operator-visible merged_prompt and the case
    # parametrisation in this file.
    if "in the style of" in prompt.lower():
        # Case 7: explicit-user-override — merge MUST short-circuit to
        # identity (heuristic 1 in :func:`_user_specified_style`).
        assert merged_prompt == prompt, (
            f"merge_visual_style should return the user prompt unchanged "
            f"when the user has already said 'in the style of': got "
            f"{merged_prompt!r}, expected {prompt!r}"
        )
    else:
        # All other cases: merge MUST append the suffix per D-15-4.
        assert merged_prompt == f"{prompt}, in the style of {visual_style}", (
            f"merge_visual_style produced unexpected output for case "
            f"{case_id!r}: got {merged_prompt!r}"
        )

    backend = _build_backend(provider, model, api_key)
    options = ImageGenOptions(size="1024x1024", count=1, quality="standard")

    result = await backend.generate(merged_prompt, options=options)

    # Structural assertions — the pytest layer.
    assert result.provider == provider, (
        f"backend reported provider {result.provider!r}, expected {provider!r}"
    )
    assert result.model == model, f"backend reported model {result.model!r}, expected {model!r}"
    assert result.latency_ms > 0.0, (
        f"backend reported non-positive latency_ms={result.latency_ms!r}"
    )
    assert len(result.images) == 1, (
        f"backend returned {len(result.images)} images, expected exactly 1 (options.count=1)"
    )

    image = result.images[0]
    assert image.image_bytes, (
        "backend returned a GeneratedImage with empty image_bytes — the "
        "service layer (T15) expects bytes-in-memory at this stage; the "
        "backend should never set image_bytes to b''"
    )
    assert image.workspace_path is None, (
        "backend returned a GeneratedImage with workspace_path populated "
        "— the backend never touches disk; T15 owns the workspace write"
    )
    assert image.media_type in {"image/png", "image/jpeg", "image/webp"}, (
        f"backend reported unexpected media_type {image.media_type!r}"
    )
    assert image.width >= 1024, (
        f"backend reported width={image.width} (< 1024); requested "
        "size=1024x1024 — likely truncated download or wrong response shape"
    )
    assert image.height >= 1024, (
        f"backend reported height={image.height} (< 1024); requested "
        "size=1024x1024 — likely truncated download or wrong response shape"
    )

    is_valid, format_hint = _looks_like_png_or_jpeg(image.image_bytes)
    assert is_valid, (
        f"backend returned bytes that do not start with a PNG or JPEG "
        f"magic-byte prefix (got format_hint={format_hint!r}; first 16 "
        f"bytes = {image.image_bytes[:16]!r}). Either the provider "
        f"returned an error envelope mis-decoded as image bytes, or the "
        f"adapter mis-parsed the response shape."
    )

    # Write the bytes + a manifest line so the operator can open the
    # artifact and judge the visual outcome.
    ext = _extension_for_media_type(image.media_type)
    output_path = tmp_path / f"{provider}_{case_id}{ext}"
    output_path.write_bytes(image.image_bytes)

    manifest_path = tmp_path / "manifest.jsonl"
    manifest_entry = {
        "case_id": case_id,
        "provider": provider,
        "model": model,
        "visual_style": visual_style,
        "user_prompt": prompt,
        "merged_prompt": merged_prompt,
        "expected_behaviour": expected_behaviour,
        "output_path": str(output_path),
        "media_type": image.media_type,
        "width": image.width,
        "height": image.height,
        "revised_prompt": image.revised_prompt,
        "latency_ms": result.latency_ms,
    }
    with manifest_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")

    # Operator-facing breadcrumb — the test framework prints this on
    # failure; on success the operator finds it via ``pytest -s``.
    print(  # noqa: T201 - operator-facing artifact pointer
        f"\n[T19 smoke] case={case_id!r} provider={provider!r} "
        f"output={output_path} merged_prompt={merged_prompt!r} "
        f"manifest={manifest_path}"
    )
