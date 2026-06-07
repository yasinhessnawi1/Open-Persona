"""Full-duplex echo integration test (spec V1 T08; binary criterion #3).

The binary acceptance criterion: audio flows client→agent AND agent→client
**SIMULTANEOUSLY**. This proves the foundation V4's barge-in requires —
a half-duplex foundation cannot retrofit interruption (kickoff §"Four
dominant concerns" #2).

The test spawns two participants in the same LiveKit Room:

1. **The agent role** — uses persona-voice's :class:`VoiceRoom` facade
   (T05) to connect, subscribe to the client's audio track, and publish
   an outbound track driven by a generated tone. Exercises VoiceRoom's
   ``connect`` / ``publish_outbound`` / ``capture_outbound_frame`` paths
   + the ``track_subscribed`` → :class:`InboundAudioFrame` drain.
2. **The client role** — raw :class:`livekit.rtc.Room` publishing a 2s
   tone inbound and subscribing to the agent's outbound.

Both audio streams flow concurrently for 2 seconds; the test asserts both
ends received frames (≥10 per direction — a generous floor given Opus
20ms framing yields ~100 frames in 2 s on a clean LAN).
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import time
from datetime import timedelta

import pytest
from livekit import api, rtc
from persona_voice.transport.room import (
    AUDIO_INBOUND_CHANNELS,
    AUDIO_INBOUND_SAMPLE_RATE,
    InboundAudioFrame,
    VoiceRoom,
)

pytestmark = [pytest.mark.integration]


# ---------- helpers --------------------------------------------------------


def _generate_sine_pcm16(
    *,
    duration_s: float,
    sample_rate: int,
    frequency_hz: float = 440.0,
    amplitude: float = 0.3,
) -> bytes:
    """Render a PCM16-LE sine-wave tone of the given duration + rate.

    Returns interleaved mono PCM16 bytes — the format LiveKit's AudioSource
    accepts. Used by both ends of the test to send recognisable audio.
    """
    n_samples = int(duration_s * sample_rate)
    amp_int = int(amplitude * 32_767)
    out = bytearray()
    for i in range(n_samples):
        val = int(amp_int * math.sin(2.0 * math.pi * frequency_hz * i / sample_rate))
        out += struct.pack("<h", val)
    return bytes(out)


def _mint_token(
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_s: int = 120,
) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(
            api.VideoGrants(
                room=room,
                room_join=True,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(seconds=ttl_s))
        .to_jwt()
    )


async def _push_tone_to_source(
    source: rtc.AudioSource,
    *,
    pcm: bytes,
    sample_rate: int,
    duration_s: float,
) -> None:
    """Push the rendered tone into the AudioSource in 20ms chunks (Opus
    default frame size) until ``duration_s`` elapses or the buffer drains."""
    frame_ms = 20
    samples_per_frame = sample_rate * frame_ms // 1000
    bytes_per_frame = samples_per_frame * 2  # PCM16 mono → 2 bytes/sample
    total = len(pcm)
    offset = 0
    deadline = time.monotonic() + duration_s
    while offset + bytes_per_frame <= total and time.monotonic() < deadline:
        chunk = pcm[offset : offset + bytes_per_frame]
        offset += bytes_per_frame
        await source.capture_frame(
            rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame,
            )
        )
        # Real-time pacing: emit every 20ms so LiveKit doesn't drop packets
        # due to buffer overrun.
        await asyncio.sleep(frame_ms / 1000)


# ---------- the binary criterion #3 test -----------------------------------


@pytest.mark.asyncio
async def test_full_duplex_audio_streams_simultaneously(
    livekit_url: str,
    livekit_api_key: str,
    livekit_api_secret: str,
    require_livekit_server: None,
) -> None:
    """The BINARY criterion #3 proof: a client publishes audio INBOUND
    while the agent simultaneously publishes audio OUTBOUND. Both ends
    receive frames; full-duplex is structurally proven before V4 needs to
    retrofit barge-in.
    """
    room_name = f"persona-voice-t08-{os.getpid()}-{int(time.monotonic() * 1000)}"

    # ---- agent role: persona-voice's VoiceRoom facade ----
    agent_room = rtc.Room()
    agent_voice_room = VoiceRoom(agent_room)

    agent_inbound_frames: list[InboundAudioFrame] = []

    async def _capture_inbound(frame: InboundAudioFrame) -> None:
        agent_inbound_frames.append(frame)

    agent_voice_room.set_inbound_handler(_capture_inbound)

    agent_token = _mint_token(
        api_key=livekit_api_key,
        api_secret=livekit_api_secret,
        identity="agent",
        room=room_name,
    )

    await agent_voice_room.connect(livekit_url, agent_token)
    assert agent_voice_room.is_connected

    agent_outbound_source = await agent_voice_room.publish_outbound()

    # ---- client role: raw rtc.Room (the V6 frontend will use the same
    # surface). Publishes a 2-second 440 Hz tone inbound and subscribes
    # to the agent's outbound.
    client_room = rtc.Room()
    client_inbound_count = {"frames": 0}

    def _on_client_track_subscribed(
        track: rtc.Track,
        _pub: rtc.TrackPublication,
        _participant: rtc.RemoteParticipant,
    ) -> None:
        if not isinstance(track, rtc.RemoteAudioTrack):
            return
        stream = rtc.AudioStream(track, sample_rate=24_000, num_channels=1)

        async def _drain() -> None:
            async for _event in stream:
                client_inbound_count["frames"] += 1

        asyncio.create_task(_drain(), name="client-drain")

    client_room.on("track_subscribed", _on_client_track_subscribed)

    client_token = _mint_token(
        api_key=livekit_api_key,
        api_secret=livekit_api_secret,
        identity="client",
        room=room_name,
    )
    await client_room.connect(livekit_url, client_token, rtc.RoomOptions(auto_subscribe=True))

    # Client publishes its own outbound track that becomes the agent's inbound.
    client_outbound_source = rtc.AudioSource(sample_rate=16_000, num_channels=1)
    client_outbound_track = rtc.LocalAudioTrack.create_audio_track(
        "client_out", client_outbound_source
    )
    await client_room.local_participant.publish_track(client_outbound_track)

    # Allow the publication metadata to propagate so both subscribers learn
    # about each other before audio starts flowing.
    await asyncio.sleep(1.0)

    # ---- simultaneous full-duplex audio for 2 seconds ----
    # Client pushes a 16kHz tone (matches the agent's inbound rail rate per
    # D-V1-6); agent pushes a 24kHz tone (matches its outbound rail rate).
    client_tone = _generate_sine_pcm16(duration_s=2.0, sample_rate=AUDIO_INBOUND_SAMPLE_RATE)
    agent_tone = _generate_sine_pcm16(duration_s=2.0, sample_rate=24_000, frequency_hz=660.0)

    push_client = asyncio.create_task(
        _push_tone_to_source(
            client_outbound_source,
            pcm=client_tone,
            sample_rate=AUDIO_INBOUND_SAMPLE_RATE,
            duration_s=2.0,
        ),
        name="t08-client-push",
    )
    push_agent = asyncio.create_task(
        _push_tone_to_source(
            agent_outbound_source,
            pcm=agent_tone,
            sample_rate=24_000,
            duration_s=2.0,
        ),
        name="t08-agent-push",
    )

    await asyncio.gather(push_client, push_agent)
    # Give the receivers a moment to drain the final frames.
    await asyncio.sleep(1.0)

    # ---- assert: BOTH ends received audio frames simultaneously ----
    # 2 seconds × 50 frames/s (20ms Opus frames) = ~100 frames expected;
    # ≥10 is the conservative floor that proves the rail is live without
    # being brittle on a busy CI host.
    assert len(agent_inbound_frames) >= 10, (
        f"agent received only {len(agent_inbound_frames)} inbound frames; "
        "expected ≥10 over 2s — inbound rail is not live"
    )
    assert client_inbound_count["frames"] >= 10, (
        f"client received only {client_inbound_count['frames']} outbound frames; "
        "expected ≥10 over 2s — outbound rail is not live"
    )
    # Every inbound frame carries its explicit sample rate per D-V1-6
    # (channels + samples_per_channel are also explicit; this catches a
    # regression where the resample seam silently drops to a different rate).
    assert all(f.sample_rate == AUDIO_INBOUND_SAMPLE_RATE for f in agent_inbound_frames)
    assert all(f.num_channels == AUDIO_INBOUND_CHANNELS for f in agent_inbound_frames)

    # ---- clean teardown ----
    await agent_voice_room.disconnect()
    await client_room.disconnect()
