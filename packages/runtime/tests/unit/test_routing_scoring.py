"""Unit tests for Spec 18 Layer 2 scoring (T10).

Covers the quality_proxy formula (D-18-5 six signals) + score_tier
(per-profile weights from D-18-2 + cost/latency/quality fit) +
partial-metadata behaviour (option (a) — returns ``None`` for absent
metadata).
"""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona_runtime.routing import RoutingContext
from persona_runtime.routing.scoring import (
    PROFILE_WEIGHTS,
    quality_proxy,
    score_tier,
)
from persona_runtime.tier import TierConfig, TierMetadata, TierRegistry


def _ctx(**overrides: object) -> RoutingContext:
    # Baseline has every signal at the zero contribution — including
    # estimated_input_tokens=0 so the token-bonus is a clean 0.0. Override
    # any field via kwargs to surface one signal at a time.
    defaults: dict[str, object] = {
        "requires_vision": False,
        "estimated_input_tokens": 0,
        "requires_strong_tools": False,
        "is_first_turn": False,
        "is_identity_sensitive": False,
        "is_boilerplate": False,
        "conversation_phase": "middle",
        "profile": "text_default",
    }
    defaults.update(overrides)
    return RoutingContext(**defaults)  # type: ignore[arg-type]


def _metadata(
    *,
    cost_in: float = 0.3,
    cost_out: float = 1.5,
    latency: float = 800.0,
    throughput: float = 60.0,
    context_window: int = 200_000,
    tool: str = "strong",
) -> TierMetadata:
    return TierMetadata(
        cost_input_per_1k_tokens=cost_in,
        cost_output_per_1k_tokens=cost_out,
        first_token_latency_ms=latency,
        throughput_tokens_per_sec=throughput,
        context_window=context_window,
        tool_strength=tool,  # type: ignore[arg-type]
    )


def _registry(*tiers: tuple[str, TierMetadata | None]) -> TierRegistry:
    return TierRegistry(
        {
            name: TierConfig(
                name=name,
                backend_config=BackendConfig(provider="anthropic", model=name, api_key="sk"),
                metadata=md,
            )
            for name, md in tiers
        }
    )


# ----- quality_proxy formula -----------------------------------------------


class TestQualityProxy:
    def test_baseline_returns_zero(self) -> None:
        # All signals off, middle phase, no tokens — score should be 0.
        assert quality_proxy(_ctx()) == pytest.approx(0.0)

    def test_first_turn_contributes_0_30(self) -> None:
        assert quality_proxy(_ctx(is_first_turn=True)) == pytest.approx(0.30)

    def test_identity_sensitive_contributes_0_30(self) -> None:
        assert quality_proxy(_ctx(is_identity_sensitive=True)) == pytest.approx(0.30)

    def test_strong_tools_contributes_0_15(self) -> None:
        assert quality_proxy(_ctx(requires_strong_tools=True)) == pytest.approx(0.15)

    def test_vision_contributes_0_10(self) -> None:
        assert quality_proxy(_ctx(requires_vision=True)) == pytest.approx(0.10)

    def test_token_signal_saturates_at_4000(self) -> None:
        # 4000 tokens → 1.0 * 0.10 = 0.10
        assert quality_proxy(_ctx(estimated_input_tokens=4000)) == pytest.approx(0.10)
        # 8000 tokens → still 0.10 (clamped)
        assert quality_proxy(_ctx(estimated_input_tokens=8000)) == pytest.approx(0.10)

    def test_phase_signal_for_opening_and_closing(self) -> None:
        # is_first_turn already 0.30, plus phase opening 0.05 = 0.35
        assert quality_proxy(_ctx(conversation_phase="opening")) == pytest.approx(0.05)
        assert quality_proxy(_ctx(conversation_phase="closing")) == pytest.approx(0.05)
        # Middle phase → no contribution.
        assert quality_proxy(_ctx(conversation_phase="middle")) == pytest.approx(0.0)

    def test_combined_signals_sum(self) -> None:
        # first_turn (0.30) + identity (0.30) + vision (0.10) = 0.70
        score = quality_proxy(
            _ctx(is_first_turn=True, is_identity_sensitive=True, requires_vision=True)
        )
        assert score == pytest.approx(0.70)

    def test_clamped_at_one(self) -> None:
        # Every signal max — total weight is 1.0; clamp at 1.0.
        score = quality_proxy(
            _ctx(
                is_first_turn=True,
                is_identity_sensitive=True,
                requires_strong_tools=True,
                requires_vision=True,
                estimated_input_tokens=10_000,
                conversation_phase="opening",
            )
        )
        assert score == pytest.approx(1.0)


# ----- score_tier (per-profile weights) ------------------------------------


class TestScoreTier:
    def test_returns_none_when_metadata_absent(self) -> None:
        registry = _registry(("mid", None))
        assert score_tier("mid", _ctx(), registry) is None

    def test_returns_float_when_metadata_present(self) -> None:
        registry = _registry(("mid", _metadata()))
        result = score_tier("mid", _ctx(), registry)
        assert result is not None
        assert 0.0 <= result <= 1.5  # not strictly [0,1] — quality_fit may push past 1.0

    def test_voice_profile_prefers_low_latency(self) -> None:
        # Two tiers — frontier slow but high quality; small fast but low quality.
        registry = _registry(
            ("frontier", _metadata(cost_in=2.0, cost_out=10.0, latency=2000.0)),
            ("small", _metadata(cost_in=0.01, cost_out=0.02, latency=100.0)),
        )
        # Voice profile weights latency at 0.60 — small (low latency) should win.
        voice_ctx = _ctx(profile="voice")
        s_frontier = score_tier("frontier", voice_ctx, registry)
        s_small = score_tier("small", voice_ctx, registry)
        assert s_frontier is not None
        assert s_small is not None
        assert s_small > s_frontier

    def test_text_profile_balances_cost_and_quality(self) -> None:
        # Frontier: high cost + high quality (matches identity-sensitive turn).
        # Small: low cost + low quality.
        registry = _registry(
            ("frontier", _metadata(cost_in=2.0, cost_out=10.0, latency=1000.0)),
            ("small", _metadata(cost_in=0.01, cost_out=0.02, latency=200.0)),
        )
        # Identity-sensitive turn → quality_proxy = 0.30 (mid-zone).
        # Neither extreme should overwhelmingly win.
        text_ctx = _ctx(profile="text_default", is_identity_sensitive=True)
        s_frontier = score_tier("frontier", text_ctx, registry)
        s_small = score_tier("small", text_ctx, registry)
        assert s_frontier is not None
        assert s_small is not None
        # Both should be in a plausible range — text balances signals.
        assert 0.0 < s_frontier < 1.5
        assert 0.0 < s_small < 1.5

    def test_quality_fit_rewards_matching_tier(self) -> None:
        # High quality_proxy turn → frontier (quality_estimate=1.0) fits best.
        registry = _registry(
            ("frontier", _metadata(cost_in=2.0, cost_out=10.0, latency=1000.0)),
            ("small", _metadata(cost_in=2.0, cost_out=10.0, latency=1000.0)),
        )
        # Identical cost + latency — only quality_fit differs.
        high_quality_ctx = _ctx(
            is_first_turn=True,
            is_identity_sensitive=True,
            requires_strong_tools=True,
            profile="text_default",
        )
        s_frontier = score_tier("frontier", high_quality_ctx, registry)
        s_small = score_tier("small", high_quality_ctx, registry)
        assert s_frontier is not None
        assert s_small is not None
        assert s_frontier > s_small


# ----- Profile weights table -----------------------------------------------


class TestProfileWeights:
    def test_text_default_weights(self) -> None:
        w = PROFILE_WEIGHTS["text_default"]
        assert w.cost == pytest.approx(0.40)
        assert w.quality == pytest.approx(0.50)
        assert w.latency == pytest.approx(0.10)

    def test_voice_weights(self) -> None:
        w = PROFILE_WEIGHTS["voice"]
        assert w.cost == pytest.approx(0.10)
        assert w.quality == pytest.approx(0.30)
        assert w.latency == pytest.approx(0.60)

    def test_weights_sum_to_one_per_profile(self) -> None:
        for profile_name, w in PROFILE_WEIGHTS.items():
            total = w.cost + w.quality + w.latency
            assert total == pytest.approx(1.0), f"profile {profile_name!r} weights don't sum to 1.0"
