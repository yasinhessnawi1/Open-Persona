"""HTTP surface for persona-voice (spec V1 T04).

The ``POST /v1/voice/token`` endpoint is the only HTTP route at v0.1 — every
other concern (signaling, audio tracks, ICE) is delegated to the LiveKit
Server (D-V1-1 branch (A) + D-V1-3). The agent worker process is a long-lived
LiveKit Room participant, not an HTTP server.
"""

from __future__ import annotations

from persona_voice.http.app import build_app, get_voice_config

__all__ = ["build_app", "get_voice_config"]
