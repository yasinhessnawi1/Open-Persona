"""Spec 13 T16 — generic vision smoke test (SCAFFOLD only).

``@pytest.mark.external`` — skipped by default per the workspace pyproject's
``addopts = "-v --tb=short -m 'not integration and not external'"``. Run
manually with::

    uv run pytest -m external -k vision_smoke

Per spec 13 Phase 2 fold-in #9 this is a scaffold: the live execution is
manual, paid, and non-deterministic and is intentionally outside CI.

What this test verifies end-to-end against a *real* third-party API:

* :func:`persona.backends.openai_compat.OpenAICompatibleBackend` constructed
  with a ``workspace_root`` correctly resolves a multimodal
  :class:`persona.schema.conversation.ConversationMessage` carrying an
  :class:`persona.schema.content.ImageContent` block (D-13-X-now option c —
  the message holds the workspace reference; the serialiser resolves bytes
  at send time and base64-encodes per D-13-2).
* The vision-capable (provider, model) pair locked at spec close-out by
  D-13-3 (``("anthropic", "claude-sonnet-4-6")`` and
  ``("openai", "gpt-4o")``) actually describes the fixture image.

The fixture at ``packages/api/tests/fixtures/vision_test_image.png`` is a
512x512 PNG containing a clearly-visible red triangle plus the text
``PERSONA TEST 13``. The assertion looks for any one of the obvious
identifiable elements (case-insensitively) — picking just one element to
match accommodates non-deterministic phrasing while still proving the
provider actually *looked at* the image (a hallucinated answer to a
vision request without the bytes would not produce any of these tokens).

Each parametrised case skips itself if the relevant API key env var is
unset, so the file is safe to ``pytest -m external`` even on a partial
key set.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from persona.backends.config import BackendConfig, Provider
from persona.backends.openai_compat import OpenAICompatibleBackend
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage
from pydantic import SecretStr

pytestmark = pytest.mark.external

# -----------------------------------------------------------------------------
# Fixture location + identifiable elements
# -----------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_IMAGE_RELPATH = "vision_test_image.png"
_IMAGE_PATH = _FIXTURES_DIR / _IMAGE_RELPATH

# Case-insensitive identifiable tokens. The model only needs to mention ONE
# of these to prove it saw the fixture (non-determinism tolerance — the
# wording varies between providers and between calls).
_IDENTIFIABLE_TOKENS = (
    "triangle",
    "red",
    "persona test 13",
    "persona test",
    "test 13",
)

# -----------------------------------------------------------------------------
# Provider matrix locked by D-13-3 at spec close-out.
# -----------------------------------------------------------------------------

_MATRIX = [
    pytest.param(
        "anthropic",
        "claude-sonnet-4-6",
        "ANTHROPIC_API_KEY",
        id="anthropic-claude-sonnet-4-6",
    ),
    pytest.param(
        "openai",
        "gpt-4o",
        "OPENAI_API_KEY",
        id="openai-gpt-4o",
    ),
]


def _now() -> datetime:
    return datetime.now(tz=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "model", "key_env"), _MATRIX)
async def test_vision_smoke_describes_fixture_image(
    provider: str,
    model: str,
    key_env: str,
) -> None:
    """Live ``chat()`` round-trip against a real vision-capable backend.

    Constructs the backend with ``workspace_root`` pointing at the
    ``packages/api/tests/fixtures/`` directory and sends a single user
    turn whose content list interleaves text and an
    :class:`ImageContent` reference. The (provider, model) pair is one
    of the two D-13-3 entries locked at spec close-out.
    """
    api_key = os.environ.get(key_env)
    if not api_key:
        pytest.skip(f"{key_env} not set; skipping live {provider} vision smoke")

    assert _IMAGE_PATH.is_file(), (
        f"fixture missing on disk: {_IMAGE_PATH} — regenerate via the T16 "
        "fixture-generation snippet in the spec-13 handover."
    )

    config = BackendConfig(
        provider=cast("Provider", provider),
        model=model,
        api_key=SecretStr(api_key),
        max_tokens=512,
        temperature=0.0,
        request_timeout_s=60.0,
    )
    backend = OpenAICompatibleBackend(config, workspace_root=_FIXTURES_DIR)

    # supports_vision is a cheap pre-flight: if D-13-3's matrix ever drops
    # this pair the smoke should fail loudly here, not after burning a
    # paid API call.
    assert backend.supports_vision, (
        f"({provider}, {model}) is not in _VISION_CAPABILITY — D-13-3 "
        "matrix changed; update _MATRIX or revisit the decision."
    )

    message = ConversationMessage(
        role="user",
        content=[
            TextContent(text="Describe this image in one short sentence."),
            ImageContent(workspace_path=_IMAGE_RELPATH, media_type="image/png"),
        ],
        created_at=_now(),
    )

    response = await backend.chat([message])

    assert response.content, "vision backend returned an empty content string"
    assert response.provider == provider
    assert response.model == model

    haystack = response.content.lower()
    matched = [tok for tok in _IDENTIFIABLE_TOKENS if tok in haystack]
    assert matched, (
        f"vision response did not mention any of "
        f"{list(_IDENTIFIABLE_TOKENS)} — the model likely did not see the "
        f"image. Response was: {response.content!r}"
    )
