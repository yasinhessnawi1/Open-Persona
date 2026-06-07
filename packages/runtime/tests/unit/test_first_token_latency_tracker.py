"""Unit tests for the Spec 18 first-token latency tracker (T06).

Covers the D-18-X-first-token-measurement-impl smoothing: simple-average
warm-up for samples 1–5, EWMA (α=0.2) from sample 6 onwards. Plus the
multi-model isolation (each model has its own state) and edge-case
validation (negative latency, invalid alpha, etc.).
"""

from __future__ import annotations

import pytest
from persona_runtime.routing import FirstTokenLatencyTracker


class TestConstructorValidation:
    def test_invalid_alpha_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            FirstTokenLatencyTracker(alpha=0.0)

    def test_invalid_alpha_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            FirstTokenLatencyTracker(alpha=-0.1)

    def test_invalid_alpha_too_large_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            FirstTokenLatencyTracker(alpha=1.5)

    def test_alpha_at_upper_bound_accepted(self) -> None:
        # alpha=1.0 means "always use the most recent sample" — valid degenerate case.
        FirstTokenLatencyTracker(alpha=1.0)

    def test_invalid_warmup_n_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="warmup_n"):
            FirstTokenLatencyTracker(warmup_n=0)


class TestEmptyState:
    def test_get_unrecorded_model_returns_none(self) -> None:
        tracker = FirstTokenLatencyTracker()
        assert tracker.get("claude-sonnet-4-6") is None

    def test_sample_count_unrecorded_model_returns_zero(self) -> None:
        tracker = FirstTokenLatencyTracker()
        assert tracker.sample_count("claude-sonnet-4-6") == 0


class TestRecordValidation:
    def test_negative_latency_rejected(self) -> None:
        tracker = FirstTokenLatencyTracker()
        with pytest.raises(ValueError, match="latency_ms"):
            tracker.record("model", -1.0)

    def test_zero_latency_accepted(self) -> None:
        # Zero is degenerate but technically a valid measurement.
        tracker = FirstTokenLatencyTracker()
        tracker.record("model", 0.0)
        assert tracker.get("model") == 0.0


class TestWarmupSimpleAverage:
    def test_single_sample_returns_that_sample(self) -> None:
        tracker = FirstTokenLatencyTracker()
        tracker.record("model", 200.0)
        assert tracker.get("model") == pytest.approx(200.0)
        assert tracker.sample_count("model") == 1

    def test_two_samples_return_average(self) -> None:
        tracker = FirstTokenLatencyTracker()
        tracker.record("model", 200.0)
        tracker.record("model", 300.0)
        assert tracker.get("model") == pytest.approx(250.0)
        assert tracker.sample_count("model") == 2

    def test_warmup_uses_simple_mean_before_ewma(self) -> None:
        # 4 samples — still in warm-up (warmup_n=5 default).
        tracker = FirstTokenLatencyTracker()
        for latency in (100.0, 200.0, 300.0, 400.0):
            tracker.record("model", latency)
        # Simple average = (100+200+300+400)/4 = 250
        assert tracker.get("model") == pytest.approx(250.0)


class TestEwmaTransition:
    def test_fifth_sample_promotes_to_ewma(self) -> None:
        tracker = FirstTokenLatencyTracker(alpha=0.2, warmup_n=5)
        for latency in (100.0, 200.0, 300.0, 400.0, 500.0):
            tracker.record("model", latency)
        # Average of 5 samples = 300; EWMA seeded with this value.
        assert tracker.get("model") == pytest.approx(300.0)
        assert tracker.sample_count("model") == 5

    def test_sixth_sample_applies_ewma(self) -> None:
        tracker = FirstTokenLatencyTracker(alpha=0.2, warmup_n=5)
        for latency in (100.0, 200.0, 300.0, 400.0, 500.0):
            tracker.record("model", latency)
        # EWMA at 300. Add a 1000ms sample.
        tracker.record("model", 1000.0)
        # 0.2 * 1000 + 0.8 * 300 = 200 + 240 = 440
        assert tracker.get("model") == pytest.approx(440.0)

    def test_outlier_after_warmup_does_not_dominate(self) -> None:
        # Five 100ms samples → EWMA seeded at 100.
        # One 3000ms outlier → EWMA = 0.2*3000 + 0.8*100 = 600+80 = 680
        # If the outlier hit during warm-up, it would have averaged into the seed
        # at much higher weight (1/5 vs the 0.2 EWMA).
        tracker = FirstTokenLatencyTracker(alpha=0.2, warmup_n=5)
        for _ in range(5):
            tracker.record("model", 100.0)
        tracker.record("model", 3000.0)
        assert tracker.get("model") == pytest.approx(680.0)


class TestMultiModelIsolation:
    def test_models_have_independent_state(self) -> None:
        tracker = FirstTokenLatencyTracker()
        tracker.record("frontier", 100.0)
        tracker.record("frontier", 200.0)
        tracker.record("mid", 50.0)
        assert tracker.get("frontier") == pytest.approx(150.0)
        assert tracker.get("mid") == pytest.approx(50.0)
        assert tracker.sample_count("frontier") == 2
        assert tracker.sample_count("mid") == 1
