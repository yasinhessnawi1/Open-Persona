"""T1 + T2b (spec P1) — the detached chat-turn worker: `ChatTurnRegistry`/`ChatTurnHandle`.

A chat turn runs as a detached ``asyncio.Task`` decoupled from the request, so a
client disconnect (navigate / reload / tab close) no longer cancels it
(D-P1-detached-execution). Mirrors ``RunRegistry`` 1:1.

T2b adds the streaming-shape parity with the old inline ``stream_chat``:
- the ``tier`` event is **captured into the terminal ``done`` payload**, not
  emitted as its own frame (D-08 gap-fix parity);
- a ``done`` event (usage / tier / budget) is emitted on CLEAN completion before
  the sentinel; an ``error`` event on loop failure (no ``done``);
- checkpoints are **throttled** (tool events flush immediately; text deltas
  debounce by char count — never per-token, D-P1-cadence);
- the credits deduct fires on the detached completion path — on CLEAN completion
  regardless of client presence, and NOT on cancel/error (the D-08-6 revision,
  D-P1-billing-contract).
"""

# ruff: noqa: ARG002 — scripted-loop signatures mirror ConversationLoop.turn intentionally.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from persona.backends.types import StreamChunk, TokenUsage
from persona.schema.conversation import Conversation
from persona_api.background.chat_turn_worker import ChatTurnHandle, ChatTurnRegistry
from persona_api.errors import TurnAlreadyActiveError
from persona_api.middleware.rls_context import current_user_id
from persona_api.sandbox import get_sandbox_request_context
from persona_runtime.agentic.events import RunEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

_OWNER = "user_alice"
_CONV = "conv_cafe"
_PERSONA = "astrid"
_MSG = "msg_assistant_1"


def _conversation() -> Conversation:
    return Conversation(conversation_id=_CONV, persona_id=_PERSONA, messages=[])


class _ScriptedLoop:
    """A fake ConversationLoop: emits a tier event, then scripted text chunks."""

    def __init__(self, deltas: list[str], *, usage: TokenUsage | None = None) -> None:
        self._deltas = deltas
        self._usage = usage
        self.seen_owner: str | None = None
        self.seen_conversation_id: str | None = None

    async def turn(
        self,
        conversation: Conversation,
        user_message: str,
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
        **_kwargs: object,
    ) -> AsyncIterator[StreamChunk]:
        self.seen_owner = current_user_id.get()
        ctx = get_sandbox_request_context()
        self.seen_conversation_id = ctx.conversation_id if ctx is not None else None
        if on_event is not None:
            await on_event(RunEvent.tier("frontier"))
        for i, delta in enumerate(self._deltas):
            is_final = i == len(self._deltas) - 1
            yield StreamChunk(
                delta=delta, is_final=is_final, usage=self._usage if is_final else None
            )


class _RecordingSink:
    """Records checkpoint + finalize calls for the orchestration assertions."""

    def __init__(self) -> None:
        self.checkpoints: list[str] = []
        self.finalize_calls: list[dict[str, Any]] = []

    def checkpoint(
        self,
        *,
        conversation_id: str,
        assistant_message_id: str,
        content: str,
        events: list[dict[str, object]],
    ) -> None:
        self.checkpoints.append(content)

    def finalize(
        self,
        *,
        conversation_id: str,
        assistant_message_id: str,
        conversation: Conversation,
        status: str,
        content: str,
        events: list[dict[str, object]],
        tier: str | None = None,
    ) -> None:
        self.finalize_calls.append({"status": status, "content": content, "tier": tier})


class _RecordingCredits:
    """A CreditsPolicy double recording deduct calls (D-P1-billing-contract gate)."""

    def __init__(self) -> None:
        self.deducts: list[tuple[str, int, str]] = []

    def deduct(self, *, rls_engine: object, user_id: str, amount: int, reason: str) -> int:
        self.deducts.append((user_id, amount, reason))
        return 0


def _registry(
    sink: _RecordingSink,
    *,
    recording_credits: _RecordingCredits | None = None,
    credits_per_turn: int = 1,
) -> ChatTurnRegistry:
    return ChatTurnRegistry(
        sink=sink,
        rls_engine=object(),  # the recording credits double ignores it
        credits_policy=recording_credits,
        credits_per_turn=credits_per_turn,
    )


def _start(
    reg: ChatTurnRegistry, loop: object, *, on_complete: object | None = None
) -> ChatTurnHandle:
    return reg.start(
        conversation_id=_CONV,
        owner_id=_OWNER,
        assistant_message_id=_MSG,
        loop=loop,
        conversation=_conversation(),
        user_message="hello",
        on_complete=on_complete,
    )


def _drain(handle: ChatTurnHandle) -> list[object]:
    items: list[object] = []
    while not handle.events.empty():
        items.append(handle.events.get_nowait())
    return items


def _kinds(items: list[object]) -> list[str | None]:
    return [None if it is None else it[0] for it in items]  # type: ignore[index]


@pytest.mark.asyncio
async def test_start_streams_chunks_then_done_then_sentinel() -> None:
    reg = _registry(_RecordingSink())
    handle = _start(reg, _ScriptedLoop(["Hel", "lo"]))
    assert handle.task is not None
    await handle.task
    # tier is folded into `done` (not a frame); two chunks, then done, then sentinel.
    assert _kinds(_drain(handle)) == ["chunk", "chunk", "done", None]


@pytest.mark.asyncio
async def test_tier_is_captured_into_done_not_emitted_as_a_frame() -> None:
    reg = _registry(_RecordingSink())
    handle = _start(reg, _ScriptedLoop(["x"]))
    await handle.task
    items = _drain(handle)
    assert "tier" not in _kinds(items)
    done = next(it for it in items if it is not None and it[0] == "done")  # type: ignore[index]
    assert done[1]["tier"] == "frontier"  # type: ignore[index]


@pytest.mark.asyncio
async def test_done_carries_usage_from_the_final_chunk() -> None:
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    reg = _registry(_RecordingSink())
    handle = _start(reg, _ScriptedLoop(["hi"], usage=usage))
    await handle.task
    done = next(it for it in _drain(handle) if it is not None and it[0] == "done")  # type: ignore[index]
    assert done[1]["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}  # type: ignore[index]


@pytest.mark.asyncio
async def test_binds_owner_and_conversation_contextvars_while_running() -> None:
    loop = _ScriptedLoop(["x"])
    handle = _start(_registry(_RecordingSink()), loop)
    await handle.task
    assert loop.seen_owner == _OWNER
    assert loop.seen_conversation_id == _CONV


@pytest.mark.asyncio
async def test_resets_contextvars_after_completion() -> None:
    handle = _start(_registry(_RecordingSink()), _ScriptedLoop(["x"]))
    await handle.task
    assert current_user_id.get() is None
    assert get_sandbox_request_context() is None


@pytest.mark.asyncio
async def test_finalize_called_once_complete_with_accumulated_content_and_tier() -> None:
    sink = _RecordingSink()
    handle = _start(_registry(sink), _ScriptedLoop(["Hel", "lo"]))
    await handle.task
    assert len(sink.finalize_calls) == 1
    assert sink.finalize_calls[0]["status"] == "complete"
    assert sink.finalize_calls[0]["content"] == "Hello"
    assert sink.finalize_calls[0]["tier"] == "frontier"


@pytest.mark.asyncio
async def test_loop_error_emits_error_frame_no_done_and_finalizes_error() -> None:
    class _BoomLoop:
        async def turn(self, *_a: object, **_k: object) -> AsyncIterator[StreamChunk]:
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

    sink = _RecordingSink()
    handle = _start(_registry(sink), _BoomLoop())
    await handle.task
    kinds = _kinds(_drain(handle))
    assert "error" in kinds
    assert "done" not in kinds
    assert sink.finalize_calls[0]["status"] == "error"
    assert get_sandbox_request_context() is None
    assert current_user_id.get() is None


@pytest.mark.asyncio
async def test_deduct_fires_once_on_clean_completion() -> None:
    recording_credits = _RecordingCredits()
    handle = _start(
        _registry(_RecordingSink(), recording_credits=recording_credits, credits_per_turn=7),
        _ScriptedLoop(["ok"]),
    )
    await handle.task
    # D-P1-billing-contract: bill on clean completion (regardless of presence).
    assert recording_credits.deducts == [(_OWNER, 7, "chat_turn")]


@pytest.mark.asyncio
async def test_deduct_not_fired_on_error() -> None:
    class _BoomLoop:
        async def turn(self, *_a: object, **_k: object) -> AsyncIterator[StreamChunk]:
            raise RuntimeError("boom")
            yield  # pragma: no cover

    recording_credits = _RecordingCredits()
    handle = _start(_registry(_RecordingSink(), recording_credits=recording_credits), _BoomLoop())
    await handle.task
    assert recording_credits.deducts == []  # error = no bill (D-08-6 unchanged for errors)


@pytest.mark.asyncio
async def test_deduct_not_fired_on_user_cancel() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingLoop:
        async def turn(self, *_a: object, **_k: object) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(delta="partial", is_final=False)
            started.set()
            await release.wait()
            yield StreamChunk(delta="never", is_final=True)  # pragma: no cover

    recording_credits = _RecordingCredits()
    reg = _registry(_RecordingSink(), recording_credits=recording_credits)
    handle = _start(reg, _BlockingLoop())
    await started.wait()
    reg.request_cancel(_CONV)
    await handle.task
    assert recording_credits.deducts == []  # explicit cancel = no bill, no partial billing


@pytest.mark.asyncio
async def test_on_complete_hook_awaited_only_on_clean_completion() -> None:
    called: list[str] = []

    async def _hook() -> None:
        called.append("done")

    handle = _start(_registry(_RecordingSink()), _ScriptedLoop(["x"]), on_complete=_hook)
    await handle.task
    assert called == ["done"]


@pytest.mark.asyncio
async def test_text_checkpoints_are_throttled_not_per_delta() -> None:
    # Three tiny deltas (well under the char threshold) → no intermediate
    # checkpoint write (the final state is persisted by finalize, not checkpoint).
    sink = _RecordingSink()
    handle = _start(_registry(sink), _ScriptedLoop(["a", "b", "c"]))
    await handle.task
    assert sink.checkpoints == []  # never per-token


@pytest.mark.asyncio
async def test_tool_event_forces_an_immediate_checkpoint() -> None:
    from persona.schema.tools import ToolCall

    class _ToolLoop:
        async def turn(
            self,
            conversation: Conversation,
            user_message: str,
            on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
            **_kwargs: object,
        ) -> AsyncIterator[StreamChunk]:
            assert on_event is not None
            await on_event(RunEvent.tool_calling(-1, [ToolCall(name="x", args={}, call_id="c1")]))
            yield StreamChunk(delta="done", is_final=True)

    sink = _RecordingSink()
    handle = _start(_registry(sink), _ToolLoop())
    await handle.task
    assert len(sink.checkpoints) >= 1  # the tool event flushed a checkpoint immediately


@pytest.mark.asyncio
async def test_second_start_same_conversation_raises_turn_already_active() -> None:
    reg = _registry(_RecordingSink())
    handle = _start(reg, _ScriptedLoop(["slow"]))
    with pytest.raises(TurnAlreadyActiveError):
        _start(reg, _ScriptedLoop(["second"]))
    await handle.task


@pytest.mark.asyncio
async def test_handle_removed_from_registry_after_completion_no_leak() -> None:
    reg = _registry(_RecordingSink())
    handle = _start(reg, _ScriptedLoop(["x"]))
    assert reg.get(_CONV) is handle
    await handle.task
    assert reg.get(_CONV) is None


@pytest.mark.asyncio
async def test_new_turn_allowed_after_previous_completes() -> None:
    reg = _registry(_RecordingSink())
    first = _start(reg, _ScriptedLoop(["x"]))
    await first.task
    second = _start(reg, _ScriptedLoop(["y"]))
    await second.task
    assert reg.get(_CONV) is None


@pytest.mark.asyncio
async def test_request_cancel_marks_cancelled_and_finalizes_partial() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingLoop:
        async def turn(self, *_a: object, **_k: object) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(delta="partial", is_final=False)
            started.set()
            await release.wait()
            yield StreamChunk(delta="never", is_final=True)  # pragma: no cover

    sink = _RecordingSink()
    reg = _registry(sink)
    handle = _start(reg, _BlockingLoop())
    await started.wait()
    reg.request_cancel(_CONV)
    await handle.task
    assert sink.finalize_calls[0]["status"] == "cancelled"
    assert sink.finalize_calls[0]["content"] == "partial"
    assert reg.get(_CONV) is None


@pytest.mark.asyncio
async def test_aclose_cancels_inflight_without_finalizing() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    class _BlockingLoop:
        async def turn(self, *_a: object, **_k: object) -> AsyncIterator[StreamChunk]:
            started.set()
            await release.wait()
            yield StreamChunk(delta="x", is_final=True)  # pragma: no cover

    sink = _RecordingSink()
    reg = _registry(sink)
    handle = _start(reg, _BlockingLoop())
    await started.wait()
    await reg.aclose()
    assert sink.finalize_calls == []
    assert handle.task is not None
    assert handle.task.cancelled()
    assert get_sandbox_request_context() is None
