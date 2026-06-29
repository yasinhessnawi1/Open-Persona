"""The persona-conditioned model side of the voice loop (spec V5).

This sub-package is the integration thread that makes the voice persona *the
same persona* as the text persona. It fills V4's ``ModelReplyProducer`` seam
with real persona-conditioned, tier-routed, streaming, cancellable generation
built by composing ``persona-runtime``'s pieces (PromptBuilder, Router,
TierRegistry, FirstTokenLatencyTracker) — never a thin "voice prompt" that
drops the persona (spec V5 §8 persona-bypass line; criteria 1+2).

T1 ships the dependency-injection container the rest of the thread composes:

* :class:`VoiceTurnContext` — the session-bound bundle of runtime collaborators
  (persona, the four typed stores, the live conversation, prompt builder,
  router, tier registry, history manager, and the optional latency tracker /
  intelligent router) that a voice turn assembles a model invocation from.
* :class:`VoiceIntegrationError` — the package's domain-error base.
"""

from __future__ import annotations

from persona_voice.model.async_lane import (
    DEFAULT_ASYNC_ARTIFACT_CONCURRENCY,
    AsyncArtifactLane,
)
from persona_voice.model.errors import VoiceIntegrationError
from persona_voice.model.history import VoiceHistoryCompactor, make_small_tier_summariser
from persona_voice.model.memory import VoiceTurnRecorder
from persona_voice.model.prompt_assembler import VoicePromptAssembler
from persona_voice.model.reply_producer import VoiceModelReplyProducer
from persona_voice.model.routing import DEFAULT_VOICE_TTFT_GATE_MS, VoiceRoutingPolicy
from persona_voice.model.tools import (
    DEFAULT_VOICE_TOOL_TIMEOUT_S,
    BoundedToolOutcome,
    DeferredArtifact,
    VoiceToolDisposition,
    VoiceToolNarrator,
    VoiceToolPolicy,
    run_tool_with_latency_bound,
)
from persona_voice.model.turn_context import REQUIRED_STORE_KINDS, VoiceTurnContext

__all__ = [
    "DEFAULT_ASYNC_ARTIFACT_CONCURRENCY",
    "DEFAULT_VOICE_TOOL_TIMEOUT_S",
    "DEFAULT_VOICE_TTFT_GATE_MS",
    "REQUIRED_STORE_KINDS",
    "AsyncArtifactLane",
    "BoundedToolOutcome",
    "DeferredArtifact",
    "VoiceHistoryCompactor",
    "VoiceIntegrationError",
    "VoiceModelReplyProducer",
    "VoicePromptAssembler",
    "VoiceRoutingPolicy",
    "VoiceToolDisposition",
    "VoiceToolNarrator",
    "VoiceToolPolicy",
    "VoiceTurnContext",
    "VoiceTurnRecorder",
    "make_small_tier_summariser",
    "run_tool_with_latency_bound",
]
