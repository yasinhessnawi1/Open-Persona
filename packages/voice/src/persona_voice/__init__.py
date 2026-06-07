"""persona-voice — the real-time voice trunk service.

Holds a live WebRTC peer connection per call via LiveKit OSS Server (D-V1-1):
signaling + STUN/TURN handled by LiveKit; persona-voice consumes the substrate
via the `livekit` Python SDK (Room / Participant / Track API). The streaming
loop skeleton (T07) ships four named Protocol seams that V2 (STT) / V3 (TTS) /
V4 (lifecycle hooks) / V5 (model entry) plug into.

See ``docs/specs/phase2/spec_V1/`` for the full spec, research, and decisions.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
