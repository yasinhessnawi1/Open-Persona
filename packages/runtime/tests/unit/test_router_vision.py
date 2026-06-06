"""Unit tests for the spec 13 vision-aware router pre-filter (T09).

Five behaviour tests plus one structural inspection test plus a
``ConversationLoop`` turn-log-visibility test (criterion #12). The structural
test is the load-bearing one: it patches ``Router._candidate_tiers`` on the
class and asserts that ``Router.choose`` invokes it on EVERY call regardless
of which downstream rule fires. A future rule that forgets to consult the
pre-filter must fail this test.

The vision capability matrix lives on each backend's ``supports_vision``
property; the router consults it through
:meth:`TierRegistry.supports_vision_for` (see D-13-X-error-hierarchy and
D-13-3 for the matrix entries).
"""

# ruff: noqa: SLF001 — tests cross-check private helpers + patch the registry cache.

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.backends.errors import NoVisionTierConfiguredError
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation, ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity, RoutingConfig
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


# ----- helpers -------------------------------------------------------------


def _persona(*, tier_for_generation: str = "auto") -> Persona:
    return Persona(
        persona_id="p1",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=[],
        ),
        worldview=[],
        routing=RoutingConfig(tier_for_generation=tier_for_generation),  # type: ignore[arg-type]
    )


def _conversation(*, turns: int = 0) -> Conversation:
    msgs = [
        ConversationMessage(role="user", content=f"m{i}", created_at=datetime.now(UTC))
        for i in range(turns)
    ]
    return Conversation(conversation_id="c1", persona_id="p1", messages=msgs)


def _registry_with(*, tiers: dict[str, ScriptedBackend]) -> TierRegistry:
    """Build a registry whose given tier names resolve to the given backends.

    The TierConfig entries are dummies; the backend cache is pre-populated so
    ``get(name)`` returns the supplied :class:`ScriptedBackend` instance —
    this is the same pattern test_loop.py uses to bypass real backend
    construction.
    """
    registry = TierRegistry(
        {name: TierConfig(name=name, backend_config=_DUMMY_CFG) for name in tiers}
    )
    registry._cache = dict(tiers)  # type: ignore[assignment]
    return registry


# ----- structural inspection (the load-bearing test) -----------------------


class TestStructuralPreFilter:
    """``Router.choose`` must call ``_candidate_tiers`` UNCONDITIONALLY.

    A future rule that returns early without consulting the filter would
    silently skip vision routing. The test patches the helper at class level
    so it is invoked for every code path; the assertion is that it fires
    exactly once per :meth:`choose` invocation, no matter which rule wins.
    """

    @pytest.mark.parametrize(
        ("turns", "message", "tier_for_generation"),
        [
            (0, "first turn", "auto"),  # first-turn-frontier rule
            (3, "thanks", "auto"),  # boilerplate-small rule
            (3, "who are you?", "auto"),  # persona-critical-frontier rule
            (3, "tell me about leases", "auto"),  # default-mid rule
            (5, "anything", "frontier"),  # override rule
        ],
    )
    def test_choose_calls_candidate_tiers_on_every_invocation(
        self, turns: int, message: str, tier_for_generation: str
    ) -> None:
        router = Router()
        registry = _registry_with(
            tiers={
                "frontier": ScriptedBackend([], supports_vision=True),
                "mid": ScriptedBackend([], supports_vision=False),
                "small": ScriptedBackend([], supports_vision=False),
            }
        )
        with patch.object(Router, "_candidate_tiers", wraps=router._candidate_tiers) as spy:
            router.choose(
                _persona(tier_for_generation=tier_for_generation),
                message,
                _conversation(turns=turns),
                turn_has_image=False,
                tier_registry=registry,
            )
        assert spy.call_count == 1


# ----- behaviour tests -----------------------------------------------------


class TestImageTurnRoutesToVisionTier:
    def test_boilerplate_image_turn_lands_on_vision_capable_tier(self) -> None:
        """A boilerplate message ("thanks") that would normally route to small
        must instead land on a vision-capable tier when the turn carries an
        image — the pre-filter strips the text-only ``small`` tier from the
        candidate set before any rule fires.
        """
        router = Router()
        registry = _registry_with(
            tiers={
                "frontier": ScriptedBackend([], supports_vision=True),
                "mid": ScriptedBackend([], supports_vision=True),
                "small": ScriptedBackend([], supports_vision=False),
            }
        )
        tier = router.choose(
            _persona(),
            "thanks",
            _conversation(turns=3),
            turn_has_image=True,
            tier_registry=registry,
        )
        assert tier != "small"
        assert tier in {"frontier", "mid"}


class TestImageTurnFallsThroughWhenFrontierMissing:
    def test_image_turn_with_only_mid_vision_capable_lands_on_mid(self) -> None:
        """Only ``{small, mid}`` configured, only ``mid`` is vision-capable:
        the first-turn frontier rule must NOT fire because ``frontier`` is
        not in candidates; the router falls through and lands on ``mid``.
        """
        router = Router()
        registry = _registry_with(
            tiers={
                "mid": ScriptedBackend([], supports_vision=True),
                "small": ScriptedBackend([], supports_vision=False),
            }
        )
        tier = router.choose(
            _persona(),
            "hello",
            _conversation(turns=0),  # would normally trigger first-turn → frontier
            turn_has_image=True,
            tier_registry=registry,
        )
        assert tier == "mid"


class TestImageTurnWithNoVisionTier:
    def test_image_turn_with_no_vision_capable_tier_raises(self) -> None:
        """Image-bearing turn but every configured tier is text-only:
        raises :class:`NoVisionTierConfiguredError` with the locked
        context shape (reason / configured_tiers).
        """
        router = Router()
        registry = _registry_with(
            tiers={
                "frontier": ScriptedBackend([], supports_vision=False),
                "mid": ScriptedBackend([], supports_vision=False),
                "small": ScriptedBackend([], supports_vision=False),
            }
        )
        with pytest.raises(NoVisionTierConfiguredError) as excinfo:
            router.choose(
                _persona(),
                "look at this",
                _conversation(turns=2),
                turn_has_image=True,
                tier_registry=registry,
            )
        ctx = excinfo.value.context
        assert ctx["reason"] == "no_vision_tier"
        # Order-preserving comma-join of every configured tier name.
        assert ctx["configured_tiers"] == "frontier,mid,small"


class TestPerTurnFiltering:
    def test_text_turn_after_image_turn_is_not_forced_to_vision_tier(self) -> None:
        """The filter is per-turn (not per-conversation). After an image
        turn, a follow-up text-only boilerplate turn lands on ``small`` as
        usual — the vision filter only restricts the candidate set on the
        turns where an image actually appears.
        """
        router = Router()
        registry = _registry_with(
            tiers={
                "frontier": ScriptedBackend([], supports_vision=True),
                "mid": ScriptedBackend([], supports_vision=True),
                "small": ScriptedBackend([], supports_vision=False),
            }
        )
        # Image turn: small is filtered out.
        first = router.choose(
            _persona(),
            "thanks",
            _conversation(turns=3),
            turn_has_image=True,
            tier_registry=registry,
        )
        assert first != "small"
        # Follow-up text turn: small is back in the candidate set and the
        # boilerplate rule routes there.
        second = router.choose(
            _persona(),
            "thanks",
            _conversation(turns=4),
            turn_has_image=False,
            tier_registry=registry,
        )
        assert second == "small"


# ----- turn-log visibility (criterion #12, fold-in #6) ---------------------


@tool(name="echo", description="Echo a message back.")
async def _echo_tool(message: str) -> object:
    from persona.schema.tools import ToolResult

    return ToolResult(tool_name="echo", content=f"echoed: {message}", is_error=False)


def _make_loop_with_registry(
    *, registry: TierRegistry, summariser_backend: ScriptedBackend
) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
    """Wire a :class:`ConversationLoop` with the supplied multi-tier registry.

    The summariser path calls ``self._tiers.get("small")``; when ``small``
    is text-only the registry's pre-populated cache hands back the supplied
    ``ScriptedBackend`` whose ``chat()`` returns the fixed "SUMMARY" response
    the loop expects.
    """
    stores = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    # Loop needs a small-tier cache entry for the summariser even when the
    # configured registry has no "small" tier (this test uses {frontier, mid}).
    # We register a private fallback so ``self._tiers.get("small")`` resolves.
    if "small" not in registry.configured_tier_names:
        registry._tiers["small"] = TierConfig(name="small", backend_config=_DUMMY_CFG)
        registry._cache["small"] = summariser_backend  # type: ignore[assignment]
    writer = MemoryTurnLogWriter()
    persona = Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="tenancy assistant",
            background="Knows husleieloven.",
            constraints=[],
        ),
    )
    loop = ConversationLoop(
        persona=persona,
        stores=stores,  # type: ignore[arg-type]
        toolbox=Toolbox([_echo_tool], allow_list=None),  # type: ignore[arg-type]
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=writer,
        max_tool_rounds=5,
    )
    return loop, writer


class TestTurnLogVisibility:
    @pytest.mark.asyncio
    async def test_image_turn_writes_routed_tier_to_turn_log(self) -> None:
        """Criterion #12: an image-bearing turn dispatched through
        ``ConversationLoop`` writes ``log.tier_used`` set to the vision-
        capable tier the router picked. Uses the spec-05 in-memory
        :class:`MemoryTurnLogWriter`.
        """
        frontier_backend = ScriptedBackend(
            [ScriptedRound(text="here is what I see")], supports_vision=False
        )
        mid_backend = ScriptedBackend(
            [ScriptedRound(text="here is what I see")], supports_vision=True
        )
        registry = _registry_with(
            tiers={
                "frontier": frontier_backend,
                "mid": mid_backend,
            }
        )
        loop, writer = _make_loop_with_registry(registry=registry, summariser_backend=mid_backend)
        conv = Conversation(conversation_id="c1", persona_id="astrid", messages=[])

        chunks = [
            c
            async for c in loop.turn(
                conv,
                "describe the attached image",
                turn_has_image=True,
            )
        ]

        # The turn streamed cleanly and the log captured the routed tier.
        assert chunks[-1].is_final is True
        assert len(writer.logs) == 1
        log = writer.logs[0]
        # First turn (turn_count == 0) would normally route to "frontier",
        # but the pre-filter strips it (text-only) so we land on "mid".
        assert log.tier_used == "mid"
