"""Unit tests for persona_voice.model.turn_context.VoiceTurnContext (spec V5 T1).

The container holds the session-bound runtime collaborators a voice turn
composes. Its only behaviour is fail-fast validation: the four typed stores
must all be present (an unconditionable persona is a persona-bypass — spec V5
§8), and ``persona_id`` is required to key store reads/writes.
"""

# ruff: noqa: ANN401, ARG002 — test double with intentionally loose signatures.

from __future__ import annotations

from typing import Any

import pytest
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry
from persona_voice.model import REQUIRED_STORE_KINDS, VoiceIntegrationError, VoiceTurnContext


class _FakeStore:
    """A no-op stand-in satisfying the MemoryStore call surface for DI tests."""

    def write(self, persona_id: str, chunks: Any, **kwargs: Any) -> None:
        return None

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[Any]:
        return []

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[Any]:
        return []

    def recent(self, persona_id: str, limit: int) -> list[Any]:
        return []

    def delete(self, persona_id: str) -> None:
        return None


def _persona(*, persona_id: str | None = "astrid") -> Persona:
    return Persona(
        persona_id=persona_id,
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding advice."],
        ),
    )


def _all_stores() -> dict[str, _FakeStore]:
    return {kind: _FakeStore() for kind in REQUIRED_STORE_KINDS}


def _dummy_registry() -> TierRegistry:
    from persona.backends import BackendConfig

    cfg = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]
    return TierRegistry({"frontier": TierConfig(name="frontier", backend_config=cfg)})


def _context(
    *,
    stores: dict[str, _FakeStore] | None = None,
    persona: Persona | None = None,
) -> VoiceTurnContext:
    return VoiceTurnContext(
        persona=persona or _persona(),
        stores=stores if stores is not None else _all_stores(),  # type: ignore[arg-type]
        conversation=Conversation(conversation_id="c1", persona_id="astrid", messages=[]),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=_dummy_registry(),
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
    )


class TestConstruction:
    def test_valid_context_constructs_with_all_four_stores(self) -> None:
        ctx = _context()
        assert ctx.persona_id == "astrid"
        assert set(ctx.stores) >= set(REQUIRED_STORE_KINDS)

    def test_optional_routing_inputs_default_to_none(self) -> None:
        ctx = _context()
        assert ctx.latency_tracker is None
        assert ctx.intelligent_router is None

    def test_is_frozen(self) -> None:
        ctx = _context()
        with pytest.raises((AttributeError, TypeError)):
            ctx.persona = _persona()  # type: ignore[misc]


class TestFailFastValidation:
    @pytest.mark.parametrize("missing", list(REQUIRED_STORE_KINDS))
    def test_missing_single_store_raises(self, missing: str) -> None:
        stores = _all_stores()
        del stores[missing]
        with pytest.raises(VoiceIntegrationError) as exc:
            _context(stores=stores)
        assert missing in str(exc.value)

    def test_missing_multiple_stores_lists_all_in_context(self) -> None:
        with pytest.raises(VoiceIntegrationError) as exc:
            _context(stores={"identity": _FakeStore()})
        rendered = str(exc.value)
        assert "self_facts" in rendered
        assert "worldview" in rendered
        assert "episodic" in rendered

    def test_empty_stores_raises(self) -> None:
        with pytest.raises(VoiceIntegrationError):
            _context(stores={})


class TestPersonaIdRequirement:
    def test_persona_without_id_raises_on_property_access(self) -> None:
        ctx = _context(persona=_persona(persona_id=None))
        with pytest.raises(VoiceIntegrationError):
            _ = ctx.persona_id
