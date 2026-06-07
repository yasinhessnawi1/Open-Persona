"""First-token latency measurement for Spec 18 routing (T06).

The Spec 18 ``UnifiedRouter`` (T09–T11) consumes the first-token latency for
each tier's backend model to inform Layer 2's latency-weighted scoring. The
existing :class:`~persona_runtime.logging.TurnLog`'s ``latency_ms`` is
**total-turn** latency, NOT first-token (verified at
``loop.py:207`` → ``loop.py:358``); this module fills the gap.

V5 R-V5-1 coordination (D-18-X-latency-measurement-source): V5's voice
profile reads the same registry field
(:attr:`TierMetadata.first_token_latency_ms`). One measurement, two
consumers — V5 doesn't need its own instrumentation.

Smoothing (D-18-X-first-token-measurement-impl):

* **Warm-up samples 1–5** — simple arithmetic mean. Prevents a single
  cold-start sample (e.g., a 3000 ms cold connection) from anchoring the
  EWMA estimate.
* **Samples 6+** — exponential moving average with α = 0.2 (moderate
  decay tuned for hour-scale traffic).

The tracker is in-process only for v0.1; no cross-restart persistence
(warm-up converges in ~5 turns). v0.2 may persist via the TurnLog JSONL
path if telemetry surfaces post-restart cold-start routing degradation
(D-18-X-latency-measurement-source).
"""

from __future__ import annotations

__all__ = ["FirstTokenLatencyTracker"]

_DEFAULT_EWMA_ALPHA = 0.2
_DEFAULT_WARMUP_N = 5


class FirstTokenLatencyTracker:
    """Per-model first-token latency tracker (Spec 18 T06).

    Thread-safe enough for v0.1 single-process API serving: dict mutations
    are GIL-atomic; the warm-up→EWMA transition is a single dict swap. No
    explicit locking; v0.2 revisits if multi-process coordination becomes a
    concern.

    Args:
        alpha: EWMA decay factor in ``(0.0, 1.0]``. Higher = more reactive
            to recent samples. Default ``0.2`` per
            D-18-X-first-token-measurement-impl.
        warmup_n: Number of warm-up samples before switching to EWMA.
            Default ``5`` per D-18-X-first-token-measurement-impl.
    """

    def __init__(
        self,
        *,
        alpha: float = _DEFAULT_EWMA_ALPHA,
        warmup_n: int = _DEFAULT_WARMUP_N,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            msg = f"alpha must be in (0.0, 1.0]; got {alpha!r}"
            raise ValueError(msg)
        if warmup_n < 1:
            msg = f"warmup_n must be >= 1; got {warmup_n!r}"
            raise ValueError(msg)
        self._alpha = alpha
        self._warmup_n = warmup_n
        self._warmup_samples: dict[str, list[float]] = {}
        self._ewma: dict[str, float] = {}

    def record(self, model_name: str, latency_ms: float) -> None:
        """Record a new first-token latency sample for ``model_name``.

        Updates the EWMA estimate if the model has graduated from warm-up
        (≥ ``warmup_n`` samples recorded); otherwise appends to the warm-up
        buffer and computes the running simple average internally.
        """
        if latency_ms < 0:
            msg = f"latency_ms must be >= 0; got {latency_ms!r}"
            raise ValueError(msg)
        if model_name in self._ewma:
            self._ewma[model_name] = (
                self._alpha * latency_ms + (1.0 - self._alpha) * self._ewma[model_name]
            )
            return
        samples = self._warmup_samples.setdefault(model_name, [])
        samples.append(latency_ms)
        if len(samples) >= self._warmup_n:
            # Promote to EWMA using the simple-average warm-up estimate.
            self._ewma[model_name] = sum(samples) / len(samples)
            del self._warmup_samples[model_name]

    def get(self, model_name: str) -> float | None:
        """Return the current first-token latency estimate for ``model_name``.

        Returns:
            The EWMA estimate (samples ≥ ``warmup_n``), the simple average
            of warm-up samples (1 ≤ samples < ``warmup_n``), or ``None``
            when no samples have been recorded for ``model_name``.
        """
        if model_name in self._ewma:
            return self._ewma[model_name]
        samples = self._warmup_samples.get(model_name)
        if not samples:
            return None
        return sum(samples) / len(samples)

    def sample_count(self, model_name: str) -> int:
        """Return the total samples recorded for ``model_name``.

        Useful for observability (e.g., a registry-state dump showing how
        well-calibrated each tier is).
        """
        if model_name in self._ewma:
            # We don't track the post-warmup count; the warmup boundary is
            # the minimum, so return at least warmup_n once graduated.
            return self._warmup_n
        return len(self._warmup_samples.get(model_name, []))
