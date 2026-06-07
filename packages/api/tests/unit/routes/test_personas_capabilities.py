"""Unit tests for the F3-T02 PersonaDetail.capabilities additive field.

Asserts the route-helper contract: ``_capabilities_from_registry`` reads
through the public :meth:`TierRegistry.supports_vision_for` +
:attr:`TierRegistry.configured_tier_names` surface
(D-F3-X-tier-registry-public-contract), never the private
``_VISION_CAPABILITY`` matrix. Returns ``None`` when the registry is not
wired (D-F3-X-capability-endpoint test path).

These tests are pure-function tests of the route helpers and do not need
the database, the runtime, or real backend construction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona_api.routes.personas import _capabilities_from_registry, _persona_detail
from persona_api.schemas import PersonaCapabilities


class _FakeRegistry:
    """Duck-typed stub matching the public TierRegistry surface F3 reads."""

    def __init__(
        self,
        *,
        tiers: tuple[str, ...],
        vision_tiers: frozenset[str],
    ) -> None:
        self._tiers = tiers
        self._vision = vision_tiers

    @property
    def configured_tier_names(self) -> tuple[str, ...]:
        return self._tiers

    def supports_vision_for(self, tier_name: str) -> bool:
        return tier_name in self._vision


def _row() -> dict[str, object]:
    """Minimal persona-row dict the helper consumes."""
    return {
        "id": "persona_test",
        "yaml": "schema_version: '1.0'\n",
        "schema_version": "1.0",
        "avatar_url": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# _capabilities_from_registry
# ---------------------------------------------------------------------------


class TestCapabilitiesFromRegistry:
    def test_returns_none_when_registry_is_none(self) -> None:
        # Composition roots without runtime (test fixtures; CLI) leave the
        # registry attribute unset; the helper returns None so PersonaDetail
        # ships without the optional capabilities field.
        assert _capabilities_from_registry(None) is None

    def test_vision_true_when_any_tier_supports_vision(self) -> None:
        # Typical 3-tier deployment where the frontier tier is vision-capable
        # (e.g. claude-sonnet-4-6) and the others are text-only.
        reg = _FakeRegistry(
            tiers=("small", "mid", "frontier"),
            vision_tiers=frozenset({"frontier"}),
        )
        caps = _capabilities_from_registry(reg)
        assert caps == PersonaCapabilities(
            vision=True, configured_tiers=("small", "mid", "frontier")
        )

    def test_vision_true_when_all_tiers_support_vision(self) -> None:
        reg = _FakeRegistry(
            tiers=("small", "mid", "frontier"),
            vision_tiers=frozenset({"small", "mid", "frontier"}),
        )
        caps = _capabilities_from_registry(reg)
        assert caps is not None
        assert caps.vision is True

    def test_vision_false_when_no_tier_supports_vision(self) -> None:
        # All-text deployment (e.g. DeepSeek-only or Ollama-only). F3's
        # composer disables image attach + tooltip per D-F3-X-no-vision-
        # surface-shape (a).
        reg = _FakeRegistry(
            tiers=("small", "mid", "frontier"),
            vision_tiers=frozenset(),
        )
        caps = _capabilities_from_registry(reg)
        assert caps == PersonaCapabilities(
            vision=False, configured_tiers=("small", "mid", "frontier")
        )

    def test_empty_registry_returns_vision_false(self) -> None:
        # Degenerate empty-registry path — no tiers configured at all.
        # `any(...)` over an empty iterable is False; capability surface
        # remains coherent (no crash, no None, just vision=False).
        reg = _FakeRegistry(tiers=(), vision_tiers=frozenset())
        caps = _capabilities_from_registry(reg)
        assert caps == PersonaCapabilities(vision=False, configured_tiers=())

    def test_preserves_insertion_order_of_tier_names(self) -> None:
        # The public TierRegistry contract preserves insertion order; F3 may
        # surface this in the disabled-attach tooltip.
        reg = _FakeRegistry(
            tiers=("frontier", "small", "mid"),
            vision_tiers=frozenset({"frontier"}),
        )
        caps = _capabilities_from_registry(reg)
        assert caps is not None
        assert caps.configured_tiers == ("frontier", "small", "mid")

    def test_uses_public_supports_vision_for_method(self) -> None:
        # Regression guard: if the helper ever crack-opens openai_compat's
        # private _VISION_CAPABILITY dict, this test should be updated to
        # reject that change. The fake registry does NOT expose any
        # private matrix; the helper works against it iff it only uses the
        # public contract.
        reg = _FakeRegistry(
            tiers=("vision_capable",),
            vision_tiers=frozenset({"vision_capable"}),
        )
        caps = _capabilities_from_registry(reg)
        assert caps is not None
        assert caps.vision is True


# ---------------------------------------------------------------------------
# _persona_detail (the hydration helper used by every persona-detail route)
# ---------------------------------------------------------------------------


class TestPersonaDetailHelper:
    def test_omits_capabilities_when_registry_none(self) -> None:
        # The additive field is optional; pre-F3 callers + test paths get
        # `capabilities=None` and the rest of PersonaDetail stays unchanged.
        detail = _persona_detail(_row(), tier_registry=None)
        assert detail.capabilities is None
        assert detail.id == "persona_test"
        assert detail.yaml == "schema_version: '1.0'\n"

    def test_populates_capabilities_when_registry_present(self) -> None:
        reg = _FakeRegistry(
            tiers=("small", "frontier"),
            vision_tiers=frozenset({"frontier"}),
        )
        detail = _persona_detail(_row(), tier_registry=reg)  # type: ignore[arg-type]
        assert detail.capabilities == PersonaCapabilities(
            vision=True, configured_tiers=("small", "frontier")
        )

    def test_capabilities_is_deployment_derived_not_persona_derived(self) -> None:
        # D-F3-X-deployment-vs-persona-capability-framing: two distinct
        # personas under the same registry must report identical capabilities.
        reg = _FakeRegistry(tiers=("frontier",), vision_tiers=frozenset({"frontier"}))
        row_a = _row() | {"id": "persona_a"}
        row_b = _row() | {"id": "persona_b"}
        detail_a = _persona_detail(row_a, tier_registry=reg)  # type: ignore[arg-type]
        detail_b = _persona_detail(row_b, tier_registry=reg)  # type: ignore[arg-type]
        assert detail_a.capabilities == detail_b.capabilities
        assert detail_a.id != detail_b.id  # they ARE different personas


# ---------------------------------------------------------------------------
# Schema-shape sanity (PersonaCapabilities is frozen + extra=forbid)
# ---------------------------------------------------------------------------


class TestPersonaCapabilitiesShape:
    def test_extra_field_is_rejected(self) -> None:
        # Defence-in-depth: _Output base sets extra="forbid"; stray fields
        # don't leak into the response.
        with pytest.raises(Exception, match="extra"):  # noqa: BLE001 — Pydantic
            PersonaCapabilities(
                vision=True,
                configured_tiers=(),
                future_field="surprise",  # type: ignore[call-arg]
            )

    def test_required_fields_must_be_present(self) -> None:
        with pytest.raises(Exception, match="vision"):  # noqa: BLE001 — Pydantic
            PersonaCapabilities(configured_tiers=())  # type: ignore[call-arg]
