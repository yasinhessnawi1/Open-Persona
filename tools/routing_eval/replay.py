"""Replay the labelled-turn fixture through :class:`UnifiedRouter` (T13).

CI-runnable: collects ``tests/test_replay.py`` and asserts each fixture entry's
``expected_tier`` matches the router's choice. New entries land via PR and
extend the regression surface — exactly like adding a test case.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from persona.backends import BackendConfig
from persona_runtime.routing import RoutingContext, UnifiedRouter
from persona_runtime.tier import TierConfig, TierMetadata, TierRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "FixtureEntry",
    "build_eval_registry",
    "load_fixture",
    "replay_fixture",
]


# The lean v0.1 production-shape registry: 3 tiers spanning cost / latency /
# quality / context-window / tool-strength variation. Mirrors the YAML
# fixture's preamble comment so the labelled expectations are reproducible.
_EVAL_REGISTRY_TIERS: tuple[tuple[str, str, bool, TierMetadata], ...] = (
    (
        "frontier",
        "claude-opus-4-7",
        True,
        TierMetadata(
            cost_input_per_1k_tokens=1.5,
            cost_output_per_1k_tokens=7.5,
            first_token_latency_ms=1200.0,
            throughput_tokens_per_sec=40.0,
            context_window=200_000,
            tool_strength="strong",
        ),
    ),
    (
        "mid",
        "claude-haiku-4-5",
        False,
        TierMetadata(
            cost_input_per_1k_tokens=0.08,
            cost_output_per_1k_tokens=0.40,
            first_token_latency_ms=400.0,
            throughput_tokens_per_sec=80.0,
            context_window=200_000,
            tool_strength="strong",
        ),
    ),
    (
        "small",
        "llama-3.1-8b",
        False,
        TierMetadata(
            cost_input_per_1k_tokens=0.005,
            cost_output_per_1k_tokens=0.008,
            first_token_latency_ms=100.0,
            throughput_tokens_per_sec=200.0,
            context_window=8_000,
            tool_strength="weak",
        ),
    ),
)


@dataclass(frozen=True)
class FixtureEntry:
    """One labelled turn from the eval fixture."""

    description: str
    context: RoutingContext
    expected_tier: str
    notes: str


def build_eval_registry() -> TierRegistry:
    """Construct the lean v0.1 production-shape registry used by :func:`replay_fixture`.

    Pre-populates the backend cache with vision-capability stubs so
    :meth:`TierRegistry.supports_vision_for` works without real backend
    construction.
    """

    class _StubBackend:
        def __init__(self, *, supports_vision: bool, model_name: str) -> None:
            self.supports_vision = supports_vision
            self.model_name = model_name

    registry = TierRegistry(
        {
            name: TierConfig(
                name=name,
                backend_config=BackendConfig(
                    provider="anthropic",
                    model=model,
                    api_key=None,
                ),
                metadata=md,
            )
            for name, model, _supports_vision, md in _EVAL_REGISTRY_TIERS
        }
    )
    # `_StubBackend` deliberately satisfies the small subset of `ChatBackend`
    # that :func:`apply_constraint_filter` reads (``supports_vision`` +
    # ``model_name``); the registry's cache slot is typed as
    # ``dict[str, ChatBackend]``, so we suppress the structural-mismatch.
    cache: dict[str, object] = {
        name: _StubBackend(supports_vision=supports_vision, model_name=model)
        for name, model, supports_vision, _ in _EVAL_REGISTRY_TIERS
    }
    registry._cache = cache  # type: ignore[assignment]  # noqa: SLF001
    return registry


def load_fixture(path: Path) -> list[FixtureEntry]:
    """Load the YAML labelled fixture from ``path``.

    Each YAML entry's ``context`` mapping is forwarded to
    :class:`RoutingContext` directly. Pydantic validates the shape; a
    malformed entry raises at load time with the offending entry's
    description so the failure mode is obvious.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries: list[FixtureEntry] = []
    for item in raw["turns"]:
        try:
            ctx = RoutingContext(**item["context"])
        except Exception as exc:
            msg = f"invalid fixture entry {item.get('description', '<no description>')!r}: {exc}"
            raise ValueError(msg) from exc
        entries.append(
            FixtureEntry(
                description=item["description"],
                context=ctx,
                expected_tier=item["expected_tier"],
                notes=item.get("notes", ""),
            )
        )
    return entries


def replay_fixture(
    fixture_path: Path,
    *,
    registry: TierRegistry | None = None,
) -> Iterator[tuple[FixtureEntry, str, bool]]:
    """Replay every fixture entry through :class:`UnifiedRouter`.

    Yields ``(entry, chosen_tier, ok)`` tuples — ``ok=True`` iff
    ``chosen_tier == entry.expected_tier``. Callers (pytest, CLI) decide how
    to surface results.
    """
    eval_registry = registry or build_eval_registry()
    router = UnifiedRouter(eval_registry)
    for entry in load_fixture(fixture_path):
        decision = router.route(entry.context)
        ok = decision.tier == entry.expected_tier
        yield entry, decision.tier, ok


def _main(argv: list[str]) -> int:
    """CLI entry: ``python -m tools.routing_eval.replay [fixture_path]``."""
    fixture_path = (
        Path(argv[1])
        if len(argv) > 1
        else Path(__file__).parent / "fixtures" / "representative_turns.yaml"
    )
    failures: list[tuple[FixtureEntry, str]] = []
    total = 0
    for entry, chosen, ok in replay_fixture(fixture_path):
        total += 1
        status = "✓" if ok else "✗"
        print(  # noqa: T201 — CLI tool
            f"  {status} {entry.description!r}: expected={entry.expected_tier!r} chosen={chosen!r}"
        )
        if not ok:
            failures.append((entry, chosen))
    print(f"\n{total - len(failures)}/{total} entries match expected tier")  # noqa: T201
    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv))


def _example_extension_point(_args: Any) -> None:  # noqa: ANN401, ARG001
    """Placeholder for v0.2 enhancements (label-set growth, quality scoring)."""
