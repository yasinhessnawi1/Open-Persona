"""Speech-activity boundary records for the V2 sensor-vs-decider port.

These records are the wire-shape V2's Silero VAD adapter (T05) emits on
the :class:`SpeechActivityListener` Protocol (T03); V4 (barge-in
decision policy) is the downstream consumer.

The exact shape is locked verbatim by D-V2-2 + research.md §R-V2-2
``v4_consumer_contract``. The sensor-vs-decider separation — a
dedicated :class:`SpeechActivityEvent` family on a dedicated Protocol
port, rather than bundling the activity stream into the V1
:class:`persona_voice.loop.streaming.STTStream` seam — is structurally
required by the Pipecat issue #1323 production bug precedent (frame
re-ordering across a shared stream caused 4× duplicate emissions);
keeping the streams separate is the only safe shape.

All records are Pydantic v2 ``frozen=True`` + ``extra="forbid"`` per
the D-05-9 boundary discipline. Cross-source ordering is NOT guaranteed
(see R-V2-2 contract-guarantee #3 — V4 must tolerate provider-end
arriving after Silero-start of the next utterance); per-source
ordering and monotonic ``ts_audio_s`` are sensor-side invariants.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SpeechActivityEvent(BaseModel):
    """Base for every sensor-emitted speech-activity event.

    Attributes:
        ts_audio_s: Position in the V1 inbound audio stream, in seconds
            since stream open. V4 uses this for correctness — correlating
            the event with the transcript it pairs with.
        ts_emit: UTC wall-clock instant the sensor published the event.
            V4 uses this for latency budgeting (R-V1-3 per-hop table).
        source: Which sensor produced the event. ``"silero"`` is the
            primary onset + offset path per D-V2-2; ``"provider"`` is
            offset-corroborator-only at v0.1; ``"webrtc"`` is reserved
            for the v0.2 fallback per the R-V2-2 falsification trigger.
        synthetic: ``True`` when the event is reconstructed from a
            transcript (provider-final without a Silero onset; see
            R-V2-2 conflict-resolution rule #4); ``False`` for
            sensor-observed transitions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts_audio_s: float
    ts_emit: datetime
    source: Literal["silero", "provider", "webrtc"]
    synthetic: bool = False


class SpeechStartedEvent(SpeechActivityEvent):
    """Speech-onset event — the user began speaking.

    ``event_type`` is the Literal discriminator V4 uses to dispatch via
    pattern-matching; pinning it as a class-level default keeps the
    field unambiguous in JSON round-trips.

    Attributes:
        event_type: Always ``"speech_started"``.
        confidence: Silero VAD probability when the source is
            ``"silero"`` (model exposes it on the activation frame);
            ``None`` for provider / webrtc / synthetic events.
    """

    event_type: Literal["speech_started"] = "speech_started"
    confidence: float | None = None


class SpeechEndedEvent(SpeechActivityEvent):
    """Speech-offset event — the user stopped speaking.

    Attributes:
        event_type: Always ``"speech_ended"``.
        transcript_settled: ``True`` iff a provider FINAL transcript
            has already fired for this utterance segment. Lets V4
            decide whether to wait for the final or act on the offset
            alone (R-V2-2 conflict-resolution rules).
        corroborates: ``True`` when the event is the second emission
            for the same offset transition — provider endpointing
            confirming a Silero offset already published. See the
            R-V2-2 at-least-once delivery contract: cross-source
            corroborations are NOT deduplicated.
        silence_duration_ms: Sensor-measured silence window leading up
            to the offset (None when not measured — e.g. synthetic
            events reconstructed from transcript timing).
    """

    event_type: Literal["speech_ended"] = "speech_ended"
    transcript_settled: bool = False
    corroborates: bool = False
    silence_duration_ms: int | None = None
