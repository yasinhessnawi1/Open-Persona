"""V5 full-turn-cycle integration test (spec V5 T9 — criterion 9).

Wires the REAL V5 generation (:class:`VoiceModelReplyProducer`) and the REAL
unified-memory recorder (:class:`VoiceTurnRecorder`) through the REAL V1
:class:`StreamingLoop` + V4 :func:`wire_orchestrated_loop`, with fakes only at the
transport / model-backend / TTS boundary (no LiveKit, no Postgres, no real API).

It drives a **multi-turn** voice conversation and asserts the full cycle:

* user transcript → persona-conditioned, tier-routed model → streamed reply →
  "spoken" via the TTS seam → written to episodic memory (the turn cycle);
* the model receives the FULL persona conditioning every turn — identity +
  constraints + retrieved memory (criteria 1+2, no bypass);
* **unified memory across turns**: what was said on turn 1 is retrieved into the
  turn-2 prompt (cross-turn recall — criterion 3 mechanism);
* what was heard is written to memory (D-V4-4 honesty path, via the loop's
  ``HeardReply`` → ``HeardWordsBridge`` → recorder).
"""

# ruff: noqa: ANN401, ARG002 — transport/model/TTS doubles with loose signatures.

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from persona.backends import StreamChunk, TokenUsage
from persona.history import ConversationHistoryManager
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity, RoutingConfig
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry
from persona_voice.loop.streaming import AudioChunk, StreamingLoop, Transcript
from persona_voice.model import VoiceModelReplyProducer, VoiceTurnContext, VoiceTurnRecorder
from persona_voice.session.state_machine import SessionStateMachine
from persona_voice.turn_taking.bridge import wire_orchestrated_loop

_VOICE_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_VOICE_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_TESTS_DIR))

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _final() -> StreamChunk:
    return StreamChunk(
        delta="",
        is_final=True,
        usage=TokenUsage(prompt_tokens=8, completion_tokens=4, total_tokens=12),
    )


class _CapturingBackend:
    """A streaming backend that records the prompt per call + replies per turn."""

    provider_name = "anthropic"
    model_name = "test-model"
    supports_native_tools = False
    supports_vision = False

    def __init__(self, replies: list[list[str]]) -> None:
        self._replies = replies
        self._call = 0
        self.prompts: list[list[Any]] = []

    async def chat_stream(
        self,
        messages: list[Any],
        *,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: Any = None,
    ) -> AsyncIterator[StreamChunk]:
        self.prompts.append(messages)
        tokens = self._replies[self._call]
        self._call += 1
        for token in tokens:
            yield StreamChunk(delta=token)
        yield _final()


class _RecordingTTS:
    """Collects the token stream it synthesises (the 'spoken' output)."""

    def __init__(self) -> None:
        self.received: list[str] = []

    async def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        async for token in text_stream:
            self.received.append(token)
            yield AudioChunk(
                data=b"\x00\x00", sample_rate=24_000, num_channels=1, samples_per_channel=1
            )

    async def cancel(self) -> None:
        return None


class _RecordingStore:
    def __init__(self, *, all_chunks: list[PersonaChunk] | None = None) -> None:
        self.chunks: list[PersonaChunk] = list(all_chunks or [])

    def write(self, persona_id: str, chunks: list[PersonaChunk], **kwargs: Any) -> None:
        self.chunks.extend(chunks)

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[PersonaChunk]:
        return list(self.chunks[:top_k])

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:
        return list(self.chunks)

    def recent(self, persona_id: str, limit: int) -> list[PersonaChunk]:
        return list(self.chunks[-limit:][::-1]) if limit > 0 else []

    def delete(self, persona_id: str) -> None:
        return None


def _voice_room_fake() -> Any:
    vr = MagicMock()
    vr.set_inbound_handler = MagicMock()

    async def _async_noop(*_a: Any, **_k: Any) -> Any:
        return MagicMock()

    vr.publish_outbound = _async_noop
    vr.capture_outbound_frame = _async_noop
    vr.clear_outbound = MagicMock(return_value=None)
    return vr


def _chunk(text: str) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 9999}", text=text, metadata={}, created_at=datetime.now(UTC)
    )


def _context(backend: _CapturingBackend) -> VoiceTurnContext:
    from persona.backends import BackendConfig

    stores = {
        "identity": _RecordingStore(all_chunks=[_chunk("I am Astrid.")]),
        "self_facts": _RecordingStore(),
        "worldview": _RecordingStore(),
        "episodic": _RecordingStore(),
    }
    cfg = BackendConfig(provider="anthropic", model="test-model", api_key=None)  # type: ignore[arg-type]
    registry = TierRegistry({"frontier": TierConfig(name="frontier", backend_config=cfg)})
    registry._cache = {"frontier": backend}  # type: ignore[assignment,dict-item]  # noqa: SLF001
    return VoiceTurnContext(
        persona=Persona(
            persona_id="astrid",
            identity=PersonaIdentity(
                name="Astrid",
                role="Norwegian tenancy law assistant",
                background="Knows husleieloven.",
                constraints=["Never give binding legal advice."],
            ),
            routing=RoutingConfig(tier_for_generation="frontier"),
        ),
        stores=stores,  # type: ignore[arg-type]
        conversation=Conversation(conversation_id="c1", persona_id="astrid", messages=[]),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
    )


async def test_full_persona_conditioned_voice_turn_cycle_multi_turn() -> None:
    backend = _CapturingBackend(
        [
            ["I'll ", "remember ", "Milo."],  # turn 1 reply
            ["Your ", "cat ", "is ", "Milo."],  # turn 2 reply
        ]
    )
    ctx = _context(backend)
    recorder = VoiceTurnRecorder(ctx)
    producer = VoiceModelReplyProducer(ctx, turn_recorder=recorder)
    tts = _RecordingTTS()
    session = SessionStateMachine(
        session_id="s1",
        user_id="u1",
        persona_id="astrid",
        conversation_id="c1",
        rls_engine=MagicMock(),
    )
    loop = StreamingLoop(voice_room=_voice_room_fake(), session=session, model=producer, tts=tts)
    # Wire V4 (orchestrator + the HeardReply→recorder bridge) the production way.
    wire_orchestrated_loop(loop=loop, session=session, turn_transcript_listener=recorder)

    # ---- Turn 1: the user tells the persona something by voice ----
    await loop.invoke_model_for_turn(
        Transcript(is_final=True, text="remember my cat is Milo", confidence=1.0)
    )

    # The reply was streamed token-by-token into the TTS seam (spoken).
    assert "".join(tts.received) == "I'll remember Milo."
    # Turn 1's prompt carried full persona conditioning (criterion 1).
    turn1_system = backend.prompts[0][0].content
    assert "You are Astrid" in turn1_system
    assert "You must NOT:" in turn1_system  # constraints enforced
    # The heard turn was written to the unified episodic store (criterion 3).
    episodic = ctx.stores["episodic"].get_all("astrid", include_superseded=True)
    assert len(episodic) == 1
    assert "remember my cat is Milo" in episodic[0].text
    assert "I'll remember Milo." in episodic[0].text  # what was HEARD (D-V4-4)
    assert len(ctx.conversation.messages) == 2  # turn appended to the conversation

    tts.received.clear()

    # ---- Turn 2: cross-turn recall — the persona remembers turn 1 ----
    await loop.invoke_model_for_turn(
        Transcript(is_final=True, text="what is my cat called?", confidence=1.0)
    )

    assert "".join(tts.received) == "Your cat is Milo."
    turn2_system = backend.prompts[1][0].content
    # Unified memory: turn 1's exchange is retrieved into turn 2's prompt.
    assert "Milo" in turn2_system
    assert "From earlier conversations:" in turn2_system
    # Persona conditioning is maintained across turns (criterion 2).
    assert "You are Astrid" in turn2_system
    assert "Never give binding legal advice." in turn2_system
    # Both turns recorded — memory accumulates.
    assert len(ctx.stores["episodic"].get_all("astrid", include_superseded=True)) == 2
    assert len(ctx.conversation.messages) == 4
