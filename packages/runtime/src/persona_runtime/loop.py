"""The ConversationLoop — the integration keystone (T07).

One ``turn(conversation, user_message)`` runs the full per-turn sequence
(spec §4.1): retrieve context → manage history → build prompt → route →
stream-generate with a tool-call sub-loop → episodic write-back → final chunk.
Everything it composes (stores, history manager, backends, toolbox, skills)
already exists and is green; this module is the conductor.

The decisions that shape this file:

- **D-05-X (sync/async summariser bridge):** the history manager is sync and
  pure. The loop predicts whether ``manage()`` will compact this turn (the same
  ``boundary > compacted_up_to`` math), pre-computes the summary by awaiting the
  small tier, then hands ``manage()`` a sync no-op assembler that returns the
  pre-computed string. Never ``asyncio.run()`` inside that callable.
- **D-05-11 (one round counter):** tool re-prompts and ``use_skill`` re-prompts
  share one ``max_tool_rounds`` budget. At the cap, append a system nudge and do
  one final tool-free generation.
- **D-05-12 (episodic write-back):** the write is the LAST step, after the
  generation loop and before the final yield. Because ``turn`` is an async
  generator, a consumer that stops iterating early (or an exception mid-stream)
  never reaches the write — a failed/cancelled turn writes nothing. No
  ``try/finally``.
- **D-05-13 (tool-call reconstruction):** streamed ``ToolCallDelta`` fragments
  are accumulated by ``call_id`` and parsed into ``ToolCall``s at stream end;
  malformed args JSON → empty dict (the ``@tool`` decorator validates).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource, make_chunk_id
from persona.schema.conversation import ConversationMessage
from persona.schema.tools import ToolCall, ToolResult
from persona.skills import collect_skill_supplements, render_skill_index
from persona.tools import format_tool_result

from persona_runtime.agentic.events import RunEvent
from persona_runtime.logging import TurnLog, estimate_cost_cents
from persona_runtime.prompt import DocumentContext, RetrievedContext
from persona_runtime.routing import (
    FirstTokenLatencyTracker,
    HeuristicRouter,
    RoutingContext,
    RoutingDecision,
    classifiers,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from persona.backends import ChatBackend, StreamChunk, TokenUsage
    from persona.history import ConversationHistoryManager
    from persona.sandbox.result import SandboxFile
    from persona.schema.conversation import Conversation
    from persona.schema.persona import Persona
    from persona.schema.skills import SkillSpec
    from persona.skills import SkillInjector, SkillScanner
    from persona.stores.protocol import MemoryStore
    from persona.tools import Toolbox

    from persona_runtime.logging import TurnLogWriter
    from persona_runtime.prompt import PromptBuilder
    from persona_runtime.routing import Router
    from persona_runtime.tier import TierRegistry

__all__ = ["ConversationLoop"]

_logger = get_logger("runtime.loop")

_RETRIEVE_TOP_K = 3
_DEFAULT_MAX_TOKENS = 4096
_MAX_TOOL_ROUNDS_NUDGE = (
    "You have used the maximum number of tool calls for this turn. "
    "Please provide your response based on the information gathered so far."
)
_SUMMARISE_INSTRUCTION = (
    "Summarise the following conversation excerpt into a short paragraph, "
    "preserving names, facts, and decisions. Be concise."
)


def _backend_max_tokens(backend: ChatBackend) -> int:
    """Best-effort context budget for the prompt builder.

    The ``ChatBackend`` Protocol does not expose ``max_tokens`` (it's on the
    backend's ``BackendConfig``), so we read it opportunistically and fall back
    to a safe default. The prompt builder treats this as the whole-prompt window.
    """
    value = getattr(backend, "max_tokens", None)
    return value if isinstance(value, int) and value > 0 else _DEFAULT_MAX_TOKENS


def _text_chunk(text: str) -> StreamChunk:
    """A non-final content chunk."""
    from persona.backends import StreamChunk

    return StreamChunk(delta=text)


def _final_chunk(usage: TokenUsage | None) -> StreamChunk:
    """The authoritative end-of-turn chunk (``is_final=True``)."""
    from persona.backends import StreamChunk

    return StreamChunk(delta="", is_final=True, usage=usage)


@dataclass
class _RoundOutcome:
    """Mutable accumulator a streamed round fills while it yields text deltas.

    Lets ``_stream_round`` yield each text delta as it arrives (so the SSE streams
    char-by-char — acceptance §6 #2) while still capturing the full text, the
    reconstructed tool calls, and the final usage for the tool sub-loop +
    episodic write-back.
    """

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None


class ConversationLoop:
    """Orchestrates a single conversation turn (spec §4).

    Pure dependency injection (engineering standards §1.2). The loop owns no
    state beyond its collaborators; it receives the ``Conversation`` per
    :meth:`turn` and never owns it (D-S05-4). The composition root owns the
    :class:`~persona_runtime.tier.TierRegistry` lifecycle, not the loop (D-05-4).
    """

    def __init__(
        self,
        *,
        persona: Persona,
        stores: dict[str, MemoryStore],
        toolbox: Toolbox,
        skill_scanner: SkillScanner,
        skill_injector: SkillInjector,
        scanned_skills: list[SkillSpec],
        history_manager: ConversationHistoryManager,
        prompt_builder: PromptBuilder,
        router: Router,
        tier_registry: TierRegistry,
        turn_log_writer: TurnLogWriter,
        max_tool_rounds: int = 5,
        latency_tracker: FirstTokenLatencyTracker | None = None,
    ) -> None:
        self._persona = persona
        self._stores = stores
        self._toolbox = toolbox
        self._scanner = skill_scanner
        self._injector = skill_injector
        self._scanned_skills = scanned_skills
        self._skills_by_name = {s.name: s for s in scanned_skills}
        self._history = history_manager
        self._builder = prompt_builder
        self._router = router
        self._tiers = tier_registry
        self._turn_log_writer = turn_log_writer
        self._max_tool_rounds = max_tool_rounds
        # Spec 18 T06: per-process first-token-latency tracker
        # (D-18-X-first-token-measurement-impl). Composition-root-owned so
        # multiple loops share a single EWMA estimate per model; an unset
        # tracker is the legacy path (no measurement, no UnifiedRouter
        # latency signal).
        self._latency_tracker = latency_tracker
        # Spec 18 T06 strangler-fig affordance: legacy callers may have
        # constructed `Router()` without a registry; ensure the router's
        # registry slot is wired so `route(context)` can do Layer 1 filtering.
        # New callers should construct `HeuristicRouter(tier_registry=...)`
        # directly. Documented mutation per D-18-X-strangler-fig-alias-shape.
        if isinstance(router, HeuristicRouter) and router._tier_registry is None:  # noqa: SLF001
            router._tier_registry = tier_registry  # noqa: SLF001
        # M1a per-turn deferred input_files (D-16-2, D-16-2-state-location).
        # Mutated by the use_skill intercept; drained by the composition
        # root's deferred_input_files_provider callable wired into the
        # code_execution factory. Cleared at every turn() entry.
        self.deferred_input_files: list[SandboxFile] = []

    async def turn(
        self,
        conversation: Conversation,
        user_message: str,
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
        *,
        turn_has_image: bool = False,
        document_context: DocumentContext | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Process one user turn, yielding StreamChunks for the response.

        Handles tool calls and ``use_skill`` activation internally (dispatch →
        feed result → re-generate, capped at ``max_tool_rounds``). Writes
        episodic memory + a :class:`TurnLog` only after generation completes
        (D-05-12). The current ``user_message`` and the assistant response are
        appended to ``conversation`` on success.

        Args:
            conversation: The live conversation (mutated on success; D-S05-4).
            user_message: This turn's user message.
            on_event: Optional async callback for granular turn events
                (``tool_calling`` / ``tool_result`` as tools dispatch, and a
                run-level ``tier`` event once the tier is chosen). Mirrors
                :meth:`AgenticLoop.run`'s ``on_event`` and reuses the SAME
                :class:`RunEvent` shapes — one event vocabulary covers both the
                chat and run-viewer SSE streams (the API maps them). Fired in
                order, interleaved with the yielded text chunks. ``None`` → the
                events are simply not surfaced (the loop's behaviour is
                otherwise unchanged). Events use ``step=-1`` (a turn is a single
                conceptual step, not the run viewer's numbered steps).
            turn_has_image: Whether the current user message carries any
                :class:`~persona.schema.content.ImageContent` block. Threaded
                through to :meth:`Router.choose` so the tier pre-filter can
                restrict the candidate set to vision-capable tiers (T13-T09).
                Caller computes this from the raw user message before
                normalising to ``str``. Defaults to ``False`` for the legacy
                text-only call sites.

        Yields:
            :class:`StreamChunk` objects ending with ``is_final=True``.
        """
        persona_id = self._require_persona_id()
        started = time.perf_counter()
        self.deferred_input_files.clear()  # M1a per-turn reset (D-16-2)

        context = self._retrieve(persona_id, user_message)
        history, compacted = await self._manage_history(conversation)
        skill_index = render_skill_index(self._scanned_skills)
        # Spec 18 T12: measure the router's OWN decision latency (distinct
        # from the whole-turn latency_ms) for D-18-X-turnlog-extension.
        routing_started = time.perf_counter()
        decision = self._decide_routing(
            user_message=user_message,
            conversation=conversation,
            turn_has_image=turn_has_image,
        )
        routing_latency_ms = (time.perf_counter() - routing_started) * 1000.0
        tier = decision.tier
        if on_event is not None:
            await on_event(RunEvent.tier(tier))
        backend = self._tiers.get(tier)
        max_tokens = _backend_max_tokens(backend)

        # Mutable per-turn state for the generation sub-loop. ``tool_messages``
        # accumulates the tool-result / system messages appended across rounds;
        # the base prompt is rebuilt each round (to pick up newly injected skill
        # content) and these are appended after it.
        rounds = 0
        skill_used: str | None = None
        matched_skill_content: str | None = None
        injected_this_turn = False
        tool_call_count = 0
        usage: TokenUsage | None = None
        assistant_text = ""
        tool_messages: list[ConversationMessage] = []

        while True:
            prompt_messages = [
                *self._builder.build(
                    self._persona,
                    context,
                    history,
                    skill_index,
                    user_message,
                    max_tokens=max_tokens,
                    matched_skill_content=matched_skill_content,
                    document_context=document_context,
                ),
                *tool_messages,
            ]
            outcome = _RoundOutcome()
            async for delta in self._stream_round(backend, prompt_messages, outcome):
                yield _text_chunk(delta)
            round_text, round_calls, round_usage = outcome.text, outcome.calls, outcome.usage
            assistant_text = round_text

            at_cap = rounds >= self._max_tool_rounds
            if round_calls and not at_cap:
                # The round's pre-tool narration (e.g. "Astrid is searching…")
                # already streamed delta-by-delta above (architecture §7.2).
                if round_usage is not None:
                    usage = round_usage
                # Surface the round's tool calls (the chat/run-viewer SSE
                # `tool_calling` event) before dispatching them.
                if on_event is not None:
                    await on_event(RunEvent.tool_calling(-1, round_calls))
                # Native tool-calling providers (OpenAI/DeepSeek/Anthropic) require
                # the assistant message that issued the tool_calls to precede the
                # matching tool results in the re-prompt — otherwise the provider
                # 400s ("'tool' must follow a message with 'tool_calls'"). Spec 11
                # soak finding. Shim providers carry the calls as text, so skip.
                if backend.supports_native_tools:
                    tool_messages.append(
                        ConversationMessage(
                            role="assistant",
                            content=round_text,
                            created_at=datetime.now(UTC),
                            tool_calls=round_calls,
                        )
                    )
                # Dispatch each call; feed results back; intercept use_skill.
                for call in round_calls:
                    result = await self._dispatch(call)
                    tool_call_count += 1
                    if on_event is not None:
                        await on_event(RunEvent.tool_result(-1, call.name, result))
                    tool_messages.append(
                        format_tool_result(call, result, provider_name=backend.provider_name)
                    )
                    if (
                        call.name == "use_skill"
                        and result.data is not None
                        and "skill_name" in result.data
                    ):
                        name = str(result.data["skill_name"])
                        if injected_this_turn:
                            tool_messages.append(
                                ConversationMessage(
                                    role="system",
                                    content=(
                                        "A skill was already activated this turn. "
                                        "Request additional skills on the next turn."
                                    ),
                                    created_at=datetime.now(UTC),
                                )
                            )
                        elif name in self._skills_by_name:
                            spec = self._skills_by_name[name]
                            matched_skill_content = await self._injector.inject(spec)
                            self.deferred_input_files.extend(collect_skill_supplements(spec))
                            skill_used = name
                            injected_this_turn = True
                # One round = one re-prompt-after-dispatch (D-05-11), regardless
                # of how many tool calls this round contained. The counter bounds
                # re-generations, not individual dispatches.
                rounds += 1
                continue

            if round_calls and at_cap:
                # Cap reached with the model still calling tools: nudge + one
                # final tool-free generation (D-05-11). Do not dispatch further.
                tool_messages.append(
                    ConversationMessage(
                        role="system",
                        content=_MAX_TOOL_ROUNDS_NUDGE,
                        created_at=datetime.now(UTC),
                    )
                )
                final_prompt = [
                    *self._builder.build(
                        self._persona,
                        context,
                        history,
                        skill_index,
                        user_message,
                        max_tokens=max_tokens,
                        matched_skill_content=matched_skill_content,
                        document_context=document_context,
                    ),
                    *tool_messages,
                ]
                final_outcome = _RoundOutcome()
                async for delta in self._stream_round(backend, final_prompt, final_outcome):
                    yield _text_chunk(delta)
                assistant_text = final_outcome.text
                round_usage = final_outcome.usage

            # Normal completion (or post-cap final text) — the text already
            # streamed delta-by-delta above. The single is_final=True chunk is
            # yielded AFTER write-back so a consumer that stops at is_final still
            # triggers the write (D-05-12).
            if round_usage is not None:
                usage = round_usage
            break

        # 7. Post-generation write-back — the LAST step before the final chunk
        #    (D-05-12). An early consumer-exit mid-stream never reaches here
        #    because the generator stays suspended at an earlier yield.
        latency_ms = (time.perf_counter() - started) * 1000.0
        self._write_episodic(persona_id, user_message, assistant_text)
        self._write_turn_log(
            conversation=conversation,
            tier=tier,
            backend=backend,
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=tool_call_count,
            skill_used=skill_used,
            compacted=compacted,
            decision=decision,
            routing_latency_ms=routing_latency_ms,
        )
        now = datetime.now(UTC)
        conversation.messages.append(
            ConversationMessage(role="user", content=user_message, created_at=now)
        )
        conversation.messages.append(
            ConversationMessage(role="assistant", content=assistant_text, created_at=now)
        )

        # 8. The authoritative end of the turn — yielded last, after write-back.
        yield _final_chunk(usage)

    # ----- internals -------------------------------------------------------

    def _require_persona_id(self) -> str:
        pid = self._persona.persona_id
        if pid is None:
            msg = "persona_id is required for the conversation loop"
            raise ValueError(msg)
        return pid

    def _retrieve(self, persona_id: str, user_message: str) -> RetrievedContext:
        """Retrieve per-turn context using the real store signatures (§4.1)."""
        identity = self._stores["identity"].get_all(persona_id)
        self_facts = self._stores["self_facts"].query(persona_id, user_message, _RETRIEVE_TOP_K)
        worldview = self._stores["worldview"].query(persona_id, user_message, _RETRIEVE_TOP_K)
        episodic = self._stores["episodic"].query(persona_id, user_message, _RETRIEVE_TOP_K)
        return RetrievedContext(
            identity=identity,
            self_facts=self_facts,
            worldview=worldview,
            episodic=episodic,
        )

    async def _manage_history(
        self, conversation: Conversation
    ) -> tuple[list[ConversationMessage], bool]:
        """Run history management with the pre-computed-summary bridge (D-05-X).

        Returns the prompt-ready history and whether compaction fired this turn.
        """
        compacted = self._will_compact(conversation)
        precomputed: str | None = None
        if compacted:
            boundary = conversation.turn_count - self._history.keep_recent
            new_range = list(conversation.messages[conversation.compacted_up_to : boundary])
            precomputed = await self._summarise(new_range)

        def _assembler(_messages: list[ConversationMessage]) -> str:
            # manage() only calls this when compacting; precomputed is set then.
            return precomputed or ""

        history = self._history.manage(conversation, summariser=_assembler)
        return history, compacted

    def _will_compact(self, conversation: Conversation) -> bool:
        """Replica of manage()'s 'will I summarise this turn?' decision (D-05-X).

        Cross-checked against the real manager in test_loop.py across K-1/K/K+1.
        """
        turn_count = conversation.turn_count
        if turn_count <= self._history.compact_every:
            return False
        boundary = turn_count - self._history.keep_recent
        return boundary > conversation.compacted_up_to

    async def _summarise(self, messages: list[ConversationMessage]) -> str:
        """Summarise an excerpt on the small tier (the one async summary call)."""
        backend = self._tiers.get("small")
        rendered = "\n".join(f"{m.role}: {m.content}" for m in messages)
        prompt = [
            ConversationMessage(
                role="system", content=_SUMMARISE_INSTRUCTION, created_at=datetime.now(UTC)
            ),
            ConversationMessage(role="user", content=rendered, created_at=datetime.now(UTC)),
        ]
        response = await backend.chat(prompt)
        return response.content.strip()

    def _decide_routing(
        self,
        *,
        user_message: str,
        conversation: Conversation,
        turn_has_image: bool,
    ) -> RoutingDecision:
        """Compute the routing decision for this turn (Spec 18 T06).

        Composition-root responsibility: applies the persona override
        short-circuit (Spec 05 rule 1, preserved per D-18-X-strangler-fig-alias-shape),
        pre-classifies the message signals (boilerplate / identity-sensitive)
        per :mod:`persona_runtime.routing.classifiers`, builds the
        :class:`RoutingContext`, and dispatches to the injected
        :class:`Router` Protocol implementation.

        Args:
            user_message: This turn's user message.
            conversation: The live conversation (for the first-turn signal).
            turn_has_image: Whether this turn carries any
                :class:`~persona.schema.content.ImageContent` — drives Layer 1.

        Returns:
            The :class:`RoutingDecision` carrying the chosen tier + model +
            rationale + observability metadata.
        """
        override = self._persona.routing.tier_for_generation
        if override != "auto":
            # Composition root handles the override — the router never sees
            # this case (Spec 05 rule 1 preserved). Build a minimal decision
            # so the TurnLog records the override path for observability.
            return RoutingDecision(
                tier=override,
                model=self._tiers.model_name_for(override),
                rationale=f"persona_override → {override}",
                candidates_considered=(override,),
            )

        routing_context = RoutingContext(
            requires_vision=turn_has_image,
            estimated_input_tokens=len(user_message) // 4,  # v0.1 cheap estimate
            requires_strong_tools=False,
            is_first_turn=(conversation.turn_count == 0),
            is_identity_sensitive=classifiers.is_persona_critical(user_message, self._persona),
            is_boilerplate=classifiers.is_boilerplate(user_message),
            conversation_phase="opening" if conversation.turn_count == 0 else "middle",
            profile="text_default",
        )
        return self._router.route(routing_context)

    async def _stream_round(
        self,
        backend: ChatBackend,
        prompt_messages: list[ConversationMessage],
        outcome: _RoundOutcome,
    ) -> AsyncIterator[str]:
        """Stream one model round, **yielding each text delta as it arrives** so the
        turn streams char-by-char (acceptance §6 #2) — while accumulating the full
        text, reconstructed tool calls, and usage into ``outcome`` for the tool
        sub-loop + episodic write-back (D-05-13).

        Tool-call deltas are accumulated by ``call_id``; args JSON is parsed at
        stream end (malformed → empty dict, fail-safe).

        Spec 18 T06 (D-18-X-first-token-measurement-impl): when a
        :class:`FirstTokenLatencyTracker` is wired into the loop, the FIRST
        non-empty ``chunk.delta`` records ``(perf_counter() - t_start) * 1000``
        as that backend's first-token latency. V5 reads the registry field;
        :class:`UnifiedRouter` reads it for Layer 2 latency scoring.
        """
        names: dict[str, str] = {}
        args_json: dict[str, str] = {}
        order: list[str] = []
        t_start = time.perf_counter()
        first_token_recorded = False
        async for chunk in backend.chat_stream(prompt_messages, tools=self._toolbox.get_specs()):
            if chunk.delta:
                if not first_token_recorded and self._latency_tracker is not None:
                    first_token_ms = (time.perf_counter() - t_start) * 1000.0
                    self._latency_tracker.record(backend.model_name, first_token_ms)
                    first_token_recorded = True
                outcome.text += chunk.delta
                yield chunk.delta
            if chunk.usage is not None:
                outcome.usage = chunk.usage
            delta = chunk.tool_call_delta
            if delta is not None:
                if delta.call_id not in names:
                    order.append(delta.call_id)
                    names[delta.call_id] = ""
                    args_json[delta.call_id] = ""
                names[delta.call_id] += delta.name_delta
                args_json[delta.call_id] += delta.arguments_delta
        outcome.calls = [self._build_call(cid, names[cid], args_json[cid]) for cid in order]

    async def _dispatch(self, call: ToolCall) -> ToolResult:
        """Dispatch a tool call, converting structural failures to is_error results.

        A hallucinated, empty, or not-allowed tool name raises ``ToolNotAllowedError``
        (spec 03, D-03-8); a registered tool that blows up raises
        ``ToolExecutionError``. Both are caught and turned into
        ``ToolResult(is_error=True, ...)`` so the model can self-correct on the
        next round instead of the error escaping the turn generator and crashing
        the SSE mid-stream ("response already started"). Mirrors the agentic
        loop's ``_dispatch`` (one tool-failure discipline across both loops).
        A tool that runs but fails already returns ``is_error=True`` unchanged.
        """
        from persona.errors import ToolExecutionError, ToolNotAllowedError

        try:
            return await self._toolbox.dispatch(call)
        except ToolNotAllowedError:
            available = ", ".join(self._toolbox.names())
            return ToolResult(
                tool_name=call.name,
                call_id=call.call_id,
                is_error=True,
                content=f"Tool '{call.name}' is not available. Available tools: {available}",
            )
        except ToolExecutionError as exc:
            return ToolResult(
                tool_name=call.name,
                call_id=call.call_id,
                is_error=True,
                content=f"Tool '{call.name}' failed: {exc}",
            )

    @staticmethod
    def _build_call(call_id: str, name: str, raw_args: str) -> ToolCall:
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {}  # fail-safe (D-05-13); the @tool decorator validates
        if not isinstance(args, dict):
            args = {}
        return ToolCall(name=name, args=args, call_id=call_id)

    def _write_episodic(self, persona_id: str, user_text: str, assistant_text: str) -> None:
        """Write one combined episodic chunk per turn (D-05-12; mirrors the CLI)."""
        store = self._stores["episodic"]
        index = len(store.get_all(persona_id, include_superseded=True))
        chunk_id = make_chunk_id(persona_id, "episodic", index)
        now = datetime.now(UTC)
        store.write(
            persona_id,
            [
                PersonaChunk(
                    id=chunk_id,
                    text=f"USER: {user_text}\nASSISTANT: {assistant_text}",
                    metadata={"importance": "0.5"},
                    created_at=now,
                    provenance=ChunkProvenance(
                        source=WriteSource.SYSTEM,
                        logical_id=chunk_id,
                        version=1,
                        written_at=now,
                        written_by="runtime.loop",
                    ),
                ),
            ],
            source=WriteSource.SYSTEM,
            written_by="runtime.loop",
        )

    def _write_turn_log(
        self,
        *,
        conversation: Conversation,
        tier: str,
        backend: ChatBackend,
        usage: TokenUsage | None,
        latency_ms: float,
        tool_calls: int,
        skill_used: str | None,
        compacted: bool,
        decision: RoutingDecision,
        routing_latency_ms: float,
    ) -> None:
        prompt_tokens = usage.prompt_tokens if usage is not None else 0
        completion_tokens = usage.completion_tokens if usage is not None else 0
        cost = estimate_cost_cents(
            backend.provider_name, backend.model_name, prompt_tokens, completion_tokens
        )
        self._turn_log_writer.write(
            TurnLog(
                conversation_id=conversation.conversation_id,
                turn_index=conversation.turn_count,
                tier_used=tier,
                model_name=backend.model_name,
                provider=backend.provider_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                cost_cents=cost,
                tool_calls=tool_calls,
                skill_used=skill_used,
                history_compacted=compacted,
                timestamp=datetime.now(UTC),
                routing_decision=decision,
                routing_latency_ms=routing_latency_ms,
                routing_fallback_triggered=decision.fallback_triggered,
                routing_fallback_reason=decision.fallback_reason,
            )
        )
