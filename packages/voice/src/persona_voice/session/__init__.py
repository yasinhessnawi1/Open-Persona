"""Session lifecycle for persona-voice (spec V1 T06).

Owns the per-call Session record, the per-session RLS engine
(D-V1-X-rls-engine-shape — one engine per WebRTC participant lifetime), and
the V4 lifecycle hook seams T07 dispatches into via the dual-priority queue.
"""

from __future__ import annotations

from persona_voice.session.state_machine import (
    Session,
    SessionEventListener,
    SessionLifecycleEvent,
    SessionState,
    SessionStateMachine,
    make_session_rls_engine,
)

__all__ = [
    "Session",
    "SessionEventListener",
    "SessionLifecycleEvent",
    "SessionState",
    "SessionStateMachine",
    "make_session_rls_engine",
]
