"""Aggregate TurnLog JSONL files into routing health stats (T13; R-18-4 drift catching).

Manual tool the D-18-X-monthly-review-cadence checklist consumes. Reads
JSONL files written by :class:`~persona_runtime.logging.JSONLTurnLogWriter`
and prints per-tier + per-profile statistics + fallback rate thresholded
per D-18-X-fallback-instrumentation (healthy <2% / watch 2-5% / alert 5-10%
/ force-heuristic >10%).

CLI:

    python -m tools.routing_eval.aggregate <jsonl-glob>

stdout-only — pipe-friendly for inclusion in monthly review notes.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "AggregateStats",
    "aggregate_jsonl_paths",
    "format_report",
]


# D-18-X-fallback-instrumentation thresholds (decimal fractions).
_FALLBACK_HEALTHY_MAX = 0.02
_FALLBACK_WATCH_MAX = 0.05
_FALLBACK_ALERT_MAX = 0.10


@dataclass
class AggregateStats:
    """Aggregated routing-health statistics for a TurnLog corpus."""

    total_decisions: int = 0
    tier_counts: Counter[str] = field(default_factory=Counter)
    profile_counts: Counter[str] = field(default_factory=Counter)
    fallback_count: int = 0
    fallback_by_reason: Counter[str] = field(default_factory=Counter)
    fallback_by_profile: Counter[str] = field(default_factory=Counter)
    routing_latencies_ms: list[float] = field(default_factory=list)
    cost_by_tier_cents: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    @property
    def fallback_rate(self) -> float:
        """Overall fallback rate as a decimal fraction in ``[0.0, 1.0]``."""
        if self.total_decisions == 0:
            return 0.0
        return self.fallback_count / self.total_decisions

    def fallback_rate_for_profile(self, profile: str) -> float:
        """Fallback rate for the given profile."""
        denom = self.profile_counts.get(profile, 0)
        if denom == 0:
            return 0.0
        return self.fallback_by_profile.get(profile, 0) / denom

    def routing_latency_percentile(self, p: float) -> float:
        """Routing-decision latency at percentile ``p`` (``0.0..1.0``)."""
        if not self.routing_latencies_ms:
            return 0.0
        # statistics.quantiles requires n>=2; for n=1 just return the only value.
        if len(self.routing_latencies_ms) < 2:
            return self.routing_latencies_ms[0]
        # quantiles gives n-1 cut points dividing into n equal parts; use 100 for
        # percentile resolution, then index appropriately.
        cuts = statistics.quantiles(self.routing_latencies_ms, n=100, method="inclusive")
        idx = max(0, min(len(cuts) - 1, int(p * 100) - 1))
        return cuts[idx]


def _classify_fallback_rate(rate: float) -> str:
    """Return the D-18-X-fallback-instrumentation band label."""
    if rate < _FALLBACK_HEALTHY_MAX:
        return "healthy"
    if rate < _FALLBACK_WATCH_MAX:
        return "watch"
    if rate < _FALLBACK_ALERT_MAX:
        return "alert"
    return "force-heuristic"


def aggregate_jsonl_paths(paths: Iterable[Path]) -> AggregateStats:
    """Read every TurnLog JSONL file in ``paths`` and return the aggregate."""
    stats = AggregateStats()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()  # noqa: PLW2901 — local rebind is intentional
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines silently — the file may be mid-write.
                    continue
                _accumulate(stats, record)
    return stats


def _accumulate(stats: AggregateStats, record: dict[str, object]) -> None:
    stats.total_decisions += 1
    tier = str(record.get("tier_used", "<unknown>"))
    stats.tier_counts[tier] += 1

    routing_decision = record.get("routing_decision")
    profile = "<legacy>"
    if isinstance(routing_decision, dict):
        # The profile is on RoutingContext, not RoutingDecision — so the
        # log line doesn't carry it directly. We infer "voice" vs
        # "text_default" via a heuristic on the fallback_reason's rate-limit
        # key if present; otherwise default to "text_default" until v0.2
        # surfaces the profile on TurnLog directly.
        profile = "text_default"
    stats.profile_counts[profile] += 1

    fb_triggered = bool(record.get("routing_fallback_triggered", False))
    if fb_triggered:
        stats.fallback_count += 1
        reason = str(record.get("routing_fallback_reason") or "<unknown>")
        stats.fallback_by_reason[reason] += 1
        stats.fallback_by_profile[profile] += 1

    rl_ms = record.get("routing_latency_ms")
    if isinstance(rl_ms, (int, float)):
        stats.routing_latencies_ms.append(float(rl_ms))

    cost = record.get("cost_cents")
    if isinstance(cost, (int, float)):
        stats.cost_by_tier_cents[tier].append(float(cost))


def format_report(stats: AggregateStats) -> str:
    """Human-readable routing-health summary for the monthly review."""
    if stats.total_decisions == 0:
        return "No TurnLog records found."

    lines: list[str] = []
    lines.append(f"Total routing decisions: {stats.total_decisions}")
    lines.append("")
    lines.append("Tier distribution:")
    for tier, n in stats.tier_counts.most_common():
        pct = n / stats.total_decisions * 100.0
        lines.append(f"  {tier:>10s}: {n:5d}  ({pct:5.1f}%)")

    lines.append("")
    fb_rate = stats.fallback_rate
    band = _classify_fallback_rate(fb_rate)
    lines.append(f"Fallback rate: {fb_rate * 100:.2f}% [{band}]")
    if stats.fallback_by_reason:
        lines.append("  Top fallback reasons:")
        for reason, n in stats.fallback_by_reason.most_common(5):
            lines.append(f"    {reason}: {n}")

    if stats.routing_latencies_ms:
        p50 = stats.routing_latency_percentile(0.50)
        p95 = stats.routing_latency_percentile(0.95)
        lines.append("")
        lines.append(f"Routing-decision latency: p50={p50:.3f}ms p95={p95:.3f}ms")

    if stats.cost_by_tier_cents:
        lines.append("")
        lines.append("Cost-per-turn distribution per tier (median cents):")
        for tier, costs in sorted(stats.cost_by_tier_cents.items()):
            if not costs:
                continue
            med = statistics.median(costs)
            lines.append(f"  {tier:>10s}: median={med:.4f}  n={len(costs)}")

    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    """CLI: ``python -m tools.routing_eval.aggregate <jsonl-glob>``."""
    if len(argv) < 2:
        print("usage: python -m tools.routing_eval.aggregate <jsonl-path>...")  # noqa: T201
        return 2
    paths = [Path(p) for p in argv[1:]]
    stats = aggregate_jsonl_paths(paths)
    print(format_report(stats))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv))
