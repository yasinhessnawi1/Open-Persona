"""R1-D-1a — ``vision_supported`` is a public capability function.

The static ``(provider, model) → bool`` lookup the backend constructor uses to
set its own ``supports_vision`` is promoted to a public name and re-exported
from ``persona.backends`` so the runtime tier registry can read vision
capability WITHOUT instantiating a backend (no API key required). One function,
one source of truth — the router pre-filter and the backend boundary read the
same lookup, so they can never diverge.
"""

from __future__ import annotations

import pytest


def test_vision_supported_is_importable_from_persona_backends() -> None:
    from persona.backends import vision_supported  # noqa: F401


def test_vision_supported_is_the_same_object_the_constructor_uses() -> None:
    # Single source of truth: the public name and the openai_compat helper the
    # OpenAICompatibleBackend constructor calls must be the same function.
    from persona.backends import openai_compat, vision_supported

    assert openai_compat.vision_supported is vision_supported


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "claude-sonnet-4-6", True),  # anthropic row is "all"
        ("openai", "gpt-4o", True),
        ("openai", "gpt-3.5-turbo", False),
        ("deepseek", "deepseek-chat", False),  # empty row → text-only
        ("ollama", "llava", False),  # not in matrix → False (R1-D-3)
        ("local", "any", False),  # HF local → matrix miss → False
        ("mystery-provider", "any", False),  # unknown provider → False
    ],
)
def test_vision_supported_resolves_statically_from_provider_model(
    provider: str, model: str, expected: bool
) -> None:
    from persona.backends import vision_supported

    assert vision_supported(provider, model) is expected
