"""persona-voice agent worker — the composition-root runner (spec V6 A0).

The minimal, single-session, dev/operator-pass-grade process that **is the
persona on a call**: it assembles the already-built V2/V3/V5 collaborators into
a running loop, joins a real LiveKit Room, and tears down on disconnect
(D-V6-X-agent-worker). Production worker-ops + deploy are forward-items.
"""

from __future__ import annotations

from persona_voice.agent.launcher import AgentLauncher, InProcessAgentLauncher
from persona_voice.agent.runner import (
    AgentSession,
    build_agent_session,
    run_agent_session,
)

__all__ = [
    "AgentLauncher",
    "AgentSession",
    "InProcessAgentLauncher",
    "build_agent_session",
    "run_agent_session",
]
