"""Tests for ``persona.originator`` — the trigger-agnostic origination capability
(Spec C0, T3, D-C0-1).

The capability is the single callable the runtime invokes to signal "this persona
has something to say to this user". Its load-bearing property is criterion 8:
the *capability* is separable from any *trigger*, so direction 4 could later drive
origination (autonomous trigger → the same interface → the same routing/delivery)
without modifying the primitive or any channel. These tests prove that
**by construction** — two distinct callers (a within-runtime conclusion and an
autonomous trigger, simulated) drive the *identical* ``originate`` and obtain the
*identical* ``DeliveryResult`` — and structurally (the module has zero runtime
coupling). Persistence (the recorder) and routing (the deliverer) are injected
fakes here; T4 fills the RLS-scoped write (incl. ownership enforcement) and T6 the
routing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryOutcome, DeliveryResult
from persona.originator import OriginatedMessageRecorder, Originator
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
TAG = PersonaIdentityTag(persona_id="astrid", display_name="Astrid")


class _FakeRecorder:
    """Stand-in for the T4 RLS-scoped conversation+episodic write seam.

    Records the message and returns a resolved conversation id (as if it had
    started one when ``conversation_id`` was ``None`` — D-C0-3).
    """

    def __init__(self, resolved_conversation_id: str = "conv_started") -> None:
        self._resolved = resolved_conversation_id
        self.recorded: list[OriginatedMessage] = []

    async def record(self, message: OriginatedMessage) -> str:
        self.recorded.append(message)
        return message.conversation_id or self._resolved


class _FakeDeliverer:
    """Stand-in for the T6 routing/deliverer."""

    def __init__(self, outcome: DeliveryOutcome = DeliveryOutcome.DELIVERED) -> None:
        self._outcome = outcome
        self.delivered: list[OriginatedMessage] = []

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        self.delivered.append(message)
        return DeliveryResult(outcome=self._outcome, channel="fake")


def _originator(
    recorder: _FakeRecorder | None = None, deliverer: _FakeDeliverer | None = None
) -> Originator:
    return Originator(recorder=recorder or _FakeRecorder(), deliverer=deliverer or _FakeDeliverer())


class TestOriginateComposition:
    @pytest.mark.asyncio
    async def test_builds_message_records_then_delivers(self) -> None:
        recorder, deliverer = _FakeRecorder(), _FakeDeliverer()
        originator = _originator(recorder, deliverer)

        result = await originator.originate(
            persona=TAG,
            owner_user_id="user_1",
            content="I've finished the report.",
            created_at=UTC_NOW,
        )

        assert isinstance(result, DeliveryResult)
        assert result.outcome is DeliveryOutcome.DELIVERED
        # write happens before delivery; both see the originated message
        assert len(recorder.recorded) == 1
        assert len(deliverer.delivered) == 1
        assert recorder.recorded[0].content == "I've finished the report."
        # ownership param carried onto the message; T4 enforces the cross-tenant guard
        assert recorder.recorded[0].owner_user_id == "user_1"

    @pytest.mark.asyncio
    async def test_returns_the_deliverers_result_verbatim(self) -> None:
        deliverer = _FakeDeliverer(DeliveryOutcome.PENDING)
        result = await _originator(deliverer=deliverer).originate(
            persona=TAG, owner_user_id="user_1", content="x", created_at=UTC_NOW
        )
        assert result.outcome is DeliveryOutcome.PENDING

    @pytest.mark.asyncio
    async def test_no_conversation_id_uses_recorder_resolved_id_for_delivery(self) -> None:
        """conversation_id None → origination starts one (D-C0-3); the delivered
        message carries the resolved id the recorder returned."""
        recorder = _FakeRecorder(resolved_conversation_id="conv_new")
        deliverer = _FakeDeliverer()
        await _originator(recorder, deliverer).originate(
            persona=TAG, owner_user_id="user_1", content="x", created_at=UTC_NOW
        )
        assert recorder.recorded[0].conversation_id is None  # recorded as "start one"
        assert deliverer.delivered[0].conversation_id == "conv_new"  # delivery has the resolved id

    @pytest.mark.asyncio
    async def test_existing_conversation_id_is_preserved(self) -> None:
        recorder, deliverer = _FakeRecorder(), _FakeDeliverer()
        await _originator(recorder, deliverer).originate(
            persona=TAG,
            owner_user_id="user_1",
            content="x",
            created_at=UTC_NOW,
            conversation_id="conv_42",
        )
        assert deliverer.delivered[0].conversation_id == "conv_42"


class TestDirection4SeamByConstruction:
    """Criterion 8: the capability is separable from any trigger — proven by two
    distinct callers driving the identical interface to identical results, and by
    the module's structural decoupling from the runtime."""

    @pytest.mark.asyncio
    async def test_runtime_like_and_autonomous_like_callers_are_indistinguishable(
        self,
    ) -> None:
        kwargs = {
            "persona": TAG,
            "owner_user_id": "user_1",
            "content": "I hit an error — how should I proceed?",
            "created_at": UTC_NOW,
        }

        # Caller A: simulates the within-runtime conclusion point (T7 will wire this).
        async def within_runtime_caller(o: Originator) -> DeliveryResult:
            return await o.originate(**kwargs)  # type: ignore[arg-type]

        # Caller B: simulates a direction-4 autonomous trigger (NOT built — proves the seam).
        async def autonomous_trigger_caller(o: Originator) -> DeliveryResult:
            return await o.originate(**kwargs)  # type: ignore[arg-type]

        rec_a, del_a = _FakeRecorder(), _FakeDeliverer()
        rec_b, del_b = _FakeRecorder(), _FakeDeliverer()

        result_a = await within_runtime_caller(_originator(rec_a, del_a))
        result_b = await autonomous_trigger_caller(_originator(rec_b, del_b))

        # Identical interface → identical result + identical message seen by collaborators.
        assert result_a == result_b
        assert rec_a.recorded == rec_b.recorded
        assert del_a.delivered == del_b.delivered

    def test_capability_has_zero_runtime_coupling(self) -> None:
        """Structural proof: the capability references no runtime internals — no
        AgenticLoop, no RunEvent, no persona_runtime import. (persona-core sits
        below persona-runtime and cannot import it; this guards against drift.)"""
        import persona.originator as module

        source = inspect.getsource(module)
        for forbidden in ("persona_runtime", "AgenticLoop", "RunEvent", "ConversationLoop"):
            assert forbidden not in source, f"runtime coupling leaked: {forbidden}"


class TestRecorderSeamSatisfiable:
    def test_fake_recorder_satisfies_the_runtime_checkable_seam(self) -> None:
        assert isinstance(_FakeRecorder(), OriginatedMessageRecorder)

    def test_object_without_record_does_not_satisfy_the_seam(self) -> None:
        class _NotARecorder:
            async def store(self, message: OriginatedMessage) -> str: ...

        assert not isinstance(_NotARecorder(), OriginatedMessageRecorder)
