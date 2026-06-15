"""The voice agent-worker — the composition-root runner (spec V6 A0).

V1–V5 built every voice component + seam, but never the deployable process that
**is the persona on a call**: ``wire_orchestrated_loop`` / ``build_voice_room``
were referenced only by tests (fakes at the transport boundary), and the V5
operator pass explicitly DEFER-NOTE'd the WebRTC mic→speaker leg "to the live-
audio leg" — i.e. to V6. This module is that missing glue: it assembles the
already-built collaborators into a running process that joins a real LiveKit
Room, runs the streaming loop, and tears down on disconnect.

**Scope (D-V6-X-agent-worker, binding).** This is the **minimal, single-session,
dev/operator-pass-grade** runner — enough to make one real browser call work
against the dev LiveKit sidecar with real keys. Production worker-ops (a
multi-session supervisor, autoscaling, room-agent dispatch) and the prod worker
deploy are explicit forward-items, the same tier as the prod-LiveKit-deploy
forward-item. This module spawns no pool, matches no dispatch, scales nothing —
one call, one session, one ``run()``.

**Layering.** persona-voice → persona-runtime → persona-core. This runner imports
``persona.*`` + ``persona_runtime.*`` directly (the V5 workspace edge) and never
``persona_api.*`` — the persona is loaded from the DB with a raw RLS-scoped
``SELECT`` (the persona-api ``RuntimeFactory._load_persona`` shape, reproduced
here so no api dependency is taken).

**The testable seam.** :func:`build_agent_session` does the heavy real assembly
(STT/TTS/model/stores/room); :class:`AgentSession` owns the connect→run→teardown
lifecycle. The lifecycle is unit-tested with fully-faked parts (so STT/TTS/DB
internals — already covered by V2–V5 — are not re-exercised); the real assembly
is exercised live by the V6 operator pass (D2).
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from persona.audit import JSONLAuditLogger
from persona.config import PersonaCoreConfig
from persona.errors import PersonaNotFoundError
from persona.history import ConversationHistoryManager
from persona.logging import get_logger
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona
from persona.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona.stores.postgres import PostgresBackend
from persona.tools import build_default_toolbox
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.routing import FirstTokenLatencyTracker
from persona_runtime.tier import tier_registry_from_env
from sqlalchemy import text

from persona_voice.model import (
    VoiceHistoryCompactor,
    VoiceModelReplyProducer,
    VoiceToolPolicy,
    VoiceTurnContext,
    VoiceTurnRecorder,
    make_small_tier_summariser,
)
from persona_voice.session.state_machine import SessionStateMachine, make_session_rls_engine
from persona_voice.stt import StreamingSTTConfig, load_streaming_stt
from persona_voice.stt.seam_adapter import V1STTStreamSeamAdapter
from persona_voice.stt.vad_silero import SileroVADAdapter
from persona_voice.tokens.issuer import mint_room_access_token
from persona_voice.transport.broadcast import DataChannelBroadcaster
from persona_voice.transport.room import VoiceRoom, build_voice_room
from persona_voice.tts._factory import load_streaming_tts
from persona_voice.tts.config import StreamingTTSConfig
from persona_voice.tts.seam_adapter import build_seam_adapter

if TYPE_CHECKING:
    from collections.abc import Callable

    from persona.stores.embedder import Embedder
    from persona.stores.protocol import MemoryStore
    from persona.tools.mcp.client import MCPClient
    from persona_runtime.tier import TierRegistry
    from sqlalchemy import Engine

    from persona_voice.config import VoiceConfig
    from persona_voice.loop.streaming import StreamingLoop

__all__ = [
    "AgentSession",
    "build_agent_session",
    "run_agent_session",
]

_logger = get_logger("agent.runner")

# A ``state_listener_factory`` that, given the session's VoiceRoom, returns the
# V6 state-broadcast listener (A1 ``DataChannelBroadcastListener``) is threaded
# through optionally — A0 is testable + runnable on its own before A1 lands, and
# A1 injects the real broadcast over the room's data channel. None ⇒ no state
# broadcast (the loop still runs).

_BGE_MODEL = "BAAI/bge-small-en-v1.5"


def _load_persona(engine: Engine, persona_id: str) -> Persona:
    """Load + validate the persona's YAML from the RLS-scoped ``personas`` row.

    Reproduces ``persona_api.services.runtime_factory.RuntimeFactory._load_persona``
    against the session RLS engine — a raw ``SELECT`` keeps persona-voice free of
    a persona-api dependency (the layering line). The engine is RLS-scoped to the
    call's owner, so a persona the caller does not own is invisible (→ not found).
    """
    import yaml

    with engine.begin() as conn:
        row = (
            conn.execute(text("SELECT yaml FROM personas WHERE id = :pid"), {"pid": persona_id})
            .mappings()
            .first()
        )
    if row is None:
        raise PersonaNotFoundError("persona not found", context={"id": persona_id})
    raw = yaml.safe_load(str(row["yaml"]))
    if isinstance(raw, dict):
        raw["persona_id"] = persona_id
    return Persona.model_validate(raw)


def _build_stores(engine: Engine, embedder: Embedder, audit_root: Path) -> dict[str, MemoryStore]:
    """The four typed stores over ``PostgresBackend`` (RLS-scoped session engine).

    Identical shape to the persona-api ``RuntimeFactory._build_stores`` so voice
    memory is the **same** unified episodic store the text path writes — a voice
    turn's memory is recalled in text and vice versa.
    """
    backend = PostgresBackend(engine=engine, embedder=embedder)
    audit = JSONLAuditLogger(audit_root)
    return {
        "identity": IdentityStore(backend=backend, audit_logger=audit),
        "self_facts": SelfFactsStore(backend=backend, audit_logger=audit),
        "worldview": WorldviewStore(backend=backend, audit_logger=audit),
        "episodic": EpisodicStore(backend=backend, audit_logger=audit),
    }


class AgentSession:
    """One voice call's running session — connect, run the loop, tear down.

    Holds the assembled collaborators and owns ONLY the lifecycle (the heavy
    assembly lives in :func:`build_agent_session`). :meth:`run` connects the
    :class:`VoiceRoom` to the LiveKit Room with the agent token, marks the
    session active, starts the streaming pipeline, waits until the user
    disconnects (the room ``disconnected`` event ends the session + sets
    :attr:`_ended`), then tears every resource down in a ``finally`` so a crash
    mid-call still releases the mic track, the RLS engine, and the MCP clients.
    """

    def __init__(
        self,
        *,
        voice_room: VoiceRoom,
        loop: StreamingLoop,
        stt_seam: V1STTStreamSeamAdapter,
        tts_seam: object,
        session: SessionStateMachine,
        mcp_clients: list[MCPClient],
        livekit_url: str,
        agent_token: str,
        ended: asyncio.Event,
    ) -> None:
        self._voice_room = voice_room
        self._loop = loop
        self._stt_seam = stt_seam
        self._tts_seam = tts_seam
        self._session = session
        self._mcp_clients = mcp_clients
        self._livekit_url = livekit_url
        self._agent_token = agent_token
        self._ended = ended

    async def run(self) -> None:
        """Join the Room, run the loop until disconnect, then tear down."""
        try:
            # Prewarm the Silero ONNX session before the first frame
            # (D-V2-X-silero pillar #3 — never first-frame).
            await self._stt_seam.load()
            await self._voice_room.connect(self._livekit_url, self._agent_token)
            await self._session.mark_active()
            await self._loop.start_pipeline()
            _logger.info(
                "voice agent session active; awaiting disconnect (session={session_id})",
                session_id=self._session.session.session_id,
            )
            await self._ended.wait()
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        """Release every resource (idempotent + best-effort).

        Order: stop the pipeline (no further model invokes) → close the STT seam
        (cancel VAD/provider drainers, close the provider socket) → cancel TTS →
        disconnect MCP clients → leave the Room → end the session (disposes the
        RLS engine + releases the advisory lock). Every step is suppressed so one
        failing teardown never strands the others.
        """
        for step in (
            self._loop.stop(),
            self._stt_seam.close(),
            self._tts_seam.cancel(),  # type: ignore[attr-defined]
        ):
            with contextlib.suppress(Exception):
                await step
        for client in self._mcp_clients:
            with contextlib.suppress(Exception):
                await client.disconnect()
        with contextlib.suppress(Exception):
            await self._voice_room.disconnect()
        # Idempotent — already ended if the user hung up; disposes the engine
        # otherwise (the crash / cancellation path).
        with contextlib.suppress(Exception):
            await self._session.end()


async def build_agent_session(
    *,
    session_id: str,
    user_id: str,
    persona_id: str,
    conversation_id: str,
    config: VoiceConfig,
    embedder: Embedder | None = None,
    tier_registry: TierRegistry | None = None,
    core_config: PersonaCoreConfig | None = None,
    stt_config: StreamingSTTConfig | None = None,
    tts_config: StreamingTTSConfig | None = None,
    audit_root: Path | None = None,
    room_factory: Callable[[], VoiceRoom] = build_voice_room,
    broadcaster_factory: Callable[[VoiceRoom], DataChannelBroadcaster] | None = None,
) -> AgentSession:
    """Assemble the real streaming voice session for one call (the heavy root).

    Loads the persona from the RLS-scoped DB row, builds the four unified stores,
    the V5 persona-conditioned producer, the real V2 STT + V3 TTS seams, and the
    V1 :class:`VoiceRoom`, wires them through :func:`wire_orchestrated_loop`, and
    mints the agent's own LiveKit token for the call's Room. Returns an
    :class:`AgentSession` ready to :meth:`~AgentSession.run`.

    ``embedder`` / ``tier_registry`` are injectable so a launcher can share these
    app-scoped, expensive singletons across calls (the persona-api
    ``RuntimeFactory`` precedent) rather than reloading bge + the tier backends
    per call. ``state_listener_factory`` injects the A1 data-channel broadcast.
    """
    # Lazy + deferred imports of orchestration wiring (kept off module import so
    # the agent package stays cheap to import for tests that only touch the
    # lifecycle). The orchestrator import would otherwise pull the full turn-
    # taking subpackage.
    from persona_voice.loop.streaming import StreamingLoop
    from persona_voice.turn_taking.bridge import wire_orchestrated_loop

    core_config = core_config or PersonaCoreConfig()
    stt_config = stt_config or StreamingSTTConfig()
    tts_config = tts_config or StreamingTTSConfig()
    audit_root = audit_root or (Path(tempfile.gettempdir()) / "persona-voice-agent-audit")
    if embedder is None:
        from persona.stores import SentenceTransformerEmbedder

        embedder = SentenceTransformerEmbedder(model_name=_BGE_MODEL)
    if tier_registry is None:
        tier_registry = tier_registry_from_env()

    # --- session RLS engine + persona + stores (tenant-isolated) ---
    rls_engine = make_session_rls_engine(config.database_url, user_id=user_id)
    persona = _load_persona(rls_engine, persona_id)
    stores = _build_stores(rls_engine, embedder, audit_root)

    # The persona's conservative voice toolbox (VoiceToolPolicy narrows the
    # offered set at generation time — D-V5-4). build_default_toolbox is async,
    # which is why this assembly root is async; its MCP clients are tracked for
    # teardown.
    toolbox, mcp_clients = await build_default_toolbox(core_config, persona)

    # --- V5 persona-conditioned, streaming, cancellable producer ---
    conversation = Conversation(conversation_id=conversation_id, persona_id=persona_id)
    tracker = FirstTokenLatencyTracker()
    ctx = VoiceTurnContext(
        persona=persona,
        stores=stores,
        conversation=conversation,
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=tier_registry,
        history_manager=ConversationHistoryManager(),
        latency_tracker=tracker,
        toolbox=toolbox,
    )
    recorder = VoiceTurnRecorder(
        ctx,
        compactor=VoiceHistoryCompactor(ctx.history_manager),
        summariser=make_small_tier_summariser(tier_registry),
    )
    producer = VoiceModelReplyProducer(
        ctx,
        tool_policy=VoiceToolPolicy(),
        turn_recorder=recorder,
    )

    # --- session state machine ---
    session = SessionStateMachine(
        session_id=session_id,
        user_id=user_id,
        persona_id=persona_id,
        conversation_id=conversation_id,
        rls_engine=rls_engine,
    )

    # --- real V2 STT seam (Deepgram + Silero); echo-mute reads the orchestrator ---
    # The VAD's TTS-mute provider needs the orchestrator's ``is_agent_speaking``,
    # but the orchestrator is built after the loop — bind it late via a holder.
    orch_holder: list[object] = []

    def _agent_speaking() -> bool:
        return bool(orch_holder) and bool(orch_holder[0].is_agent_speaking())  # type: ignore[attr-defined]

    stt_backend = load_streaming_stt(stt_config)
    vad = SileroVADAdapter(stt_config, session_state_provider=_agent_speaking)
    stt_seam = V1STTStreamSeamAdapter(backend=stt_backend, vad=vad)

    # --- real V3 TTS seam bound to THIS persona's voice ---
    tts_backend = load_streaming_tts(tts_config)
    tts_seam = build_seam_adapter(
        backend=tts_backend,
        config=tts_config,
        voice_spec=persona.identity.voice,
    )

    # --- transport + loop + orchestrator ---
    voice_room = room_factory()
    loop = StreamingLoop(
        voice_room=voice_room,
        session=session,
        stt=stt_seam,
        tts=tts_seam,
        model=producer,
    )
    # The A1 data-channel broadcaster implements BOTH the V4 state-listener seam
    # (orb) AND the V6 caption-listener seam (captions) over one room+topic, so it
    # wires into both. Default-built over this call's room; injectable for tests.
    broadcaster = broadcaster_factory(voice_room) if broadcaster_factory else (
        DataChannelBroadcaster(voice_room)
    )
    orchestrator = wire_orchestrated_loop(
        loop=loop,
        session=session,
        state_listener=broadcaster,
        turn_transcript_listener=recorder,
    )
    loop.caption_listener = broadcaster
    orch_holder.append(orchestrator)
    # The STT seam dispatches speech-activity events to its listener — the
    # orchestrator (so VAD onset/offset drive the conversational state machine).
    stt_seam.listener = orchestrator

    # --- room disconnect → end the session + release the run() awaiter ---
    ended = asyncio.Event()

    async def _on_room_disconnected() -> None:
        await session.end()
        ended.set()

    voice_room.set_disconnect_handler(_on_room_disconnected)

    # --- the agent's own LiveKit token for THIS call's Room ---
    # Distinct identity from the user (LiveKit rejects duplicate identities in a
    # Room); same deterministic room name (``persona:{session_id}``); the grants
    # already include can_subscribe (user mic) + can_publish (persona audio) +
    # can_publish_data (the A1 state/transcript broadcast).
    agent_token = mint_room_access_token(
        api_key=config.livekit_api_key.get_secret_value(),
        api_secret=config.livekit_api_secret.get_secret_value(),
        livekit_url=config.livekit_url,
        session_id=session_id,
        user_id=f"agent:{session_id}",
        persona_id=persona_id,
        conversation_id=conversation_id,
        ttl_s=config.livekit_token_ttl_s,
    )
    # mcp_clients accumulated by build_default_toolbox are closed at teardown.
    return AgentSession(
        voice_room=voice_room,
        loop=loop,
        stt_seam=stt_seam,
        tts_seam=tts_seam,
        session=session,
        mcp_clients=mcp_clients,
        livekit_url=agent_token.livekit_url,
        agent_token=agent_token.token,
        ended=ended,
    )


async def run_agent_session(
    *,
    session_id: str,
    user_id: str,
    persona_id: str,
    conversation_id: str,
    config: VoiceConfig,
    embedder: Embedder | None = None,
    tier_registry: TierRegistry | None = None,
    core_config: PersonaCoreConfig | None = None,
    broadcaster_factory: Callable[[VoiceRoom], DataChannelBroadcaster] | None = None,
) -> None:
    """Build + run one voice agent session to completion (the convenience entry).

    The single coroutine the dev launcher (:mod:`persona_voice.agent.launcher`)
    spawns per call: assemble the real session, then run its connect→loop→teardown
    lifecycle. Any exception propagates to the launcher, which logs it (a failed
    agent must never take down the token endpoint).
    """
    session = await build_agent_session(
        session_id=session_id,
        user_id=user_id,
        persona_id=persona_id,
        conversation_id=conversation_id,
        config=config,
        embedder=embedder,
        tier_registry=tier_registry,
        core_config=core_config,
        broadcaster_factory=broadcaster_factory,
    )
    await session.run()
