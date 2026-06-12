"""T13 (§2.6 / D-25-7 / D-25-X-nvidia-model-name-normalization) — NVIDIA
price-table entries, ``cost_basis`` flag, and catalog-prefix normalization.

The §2.6 production bug: NVIDIA turns logged ``cost=0`` because the catalog
model ID arrives prefixed (``"nvidia/..."``) while the table is bare-keyed.
These tests pin (a) NVIDIA entries exist + estimate non-zero, (b) the
prefix-normalization resolves a prefixed ID, (c) ``cost_basis_for`` flags
NVIDIA "verify-at-deploy" and everything else "published".
"""

from __future__ import annotations

import pytest
from persona_runtime.logging import cost_basis_for, estimate_cost_cents

_NVIDIA_MODELS = [
    "llama-3.3-nemotron-super-49b-v1.5",
    "nemotron-3-super-120b-a12b",
    "nemotron-3-nano-omni-30b-a3b-reasoning",
]


@pytest.mark.parametrize("model", _NVIDIA_MODELS)
def test_nvidia_bare_key_estimates_nonzero(model: str) -> None:
    cost = estimate_cost_cents("nvidia", model, prompt_tokens=1000, completion_tokens=1000)
    assert cost > 0.0


@pytest.mark.parametrize("model", _NVIDIA_MODELS)
def test_nvidia_catalog_prefix_is_normalized(model: str) -> None:
    """A ``"nvidia/<model>"`` catalog ID resolves to the same estimate as bare."""
    bare = estimate_cost_cents("nvidia", model, 1000, 1000)
    prefixed = estimate_cost_cents("nvidia", f"nvidia/{model}", 1000, 1000)
    assert prefixed == bare > 0.0


def test_super_49b_exact_estimate() -> None:
    # (0.040, 0.040) cents/1k → 1000 prompt + 1000 completion = 0.08 cents.
    cost = estimate_cost_cents("nvidia", "llama-3.3-nemotron-super-49b-v1.5", 1000, 1000)
    assert cost == pytest.approx(0.08)


def test_unknown_model_still_zero() -> None:
    assert estimate_cost_cents("nvidia", "no-such-model", 1000, 1000) == 0.0


@pytest.mark.parametrize("model", _NVIDIA_MODELS)
def test_nvidia_cost_basis_verify_at_deploy(model: str) -> None:
    assert cost_basis_for("nvidia", model) == "verify-at-deploy"
    # Also via the prefixed catalog ID.
    assert cost_basis_for("nvidia", f"nvidia/{model}") == "verify-at-deploy"


def test_published_provider_cost_basis() -> None:
    assert cost_basis_for("anthropic", "claude-sonnet-4-6") == "published"
    # Unknown pairs default to published (no shadow-price flag).
    assert cost_basis_for("openai", "gpt-whatever") == "published"


def test_prefix_strip_only_matches_provider() -> None:
    """A non-provider leading segment is NOT stripped (no false normalization)."""
    # "stabilityai/x" under provider nvidia keeps its slash → unknown → 0.
    assert estimate_cost_cents("nvidia", "stabilityai/x", 1000, 1000) == 0.0
