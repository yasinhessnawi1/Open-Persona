"""Conversation-boundary logic (Spec C1 T8, §3) — the pure idle predicate.

`/new` and the idle-timeout are the only operations that END a persona's
conversation (per §3 — switching suspends, never ends). Both are
per-persona-per-channel: one persona's boundary never touches another's. The
transactional operations (reassign on `/new`, sweep on idle) live in the infra
store; this module is the pure, deterministic predicate the idle sweep applies.

Owned surface — api-free; stdlib only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta

__all__ = ["is_idle_expired"]


def is_idle_expired(last_activity_at: datetime, *, now: datetime, idle_after: timedelta) -> bool:
    """Whether a conversation idle since ``last_activity_at`` has timed out.

    Strict ``>``: a gap of exactly ``idle_after`` is still within the window; only
    a longer gap expires. ``now``/``idle_after`` are injected so the rule is pure
    and deterministic (no wall-clock read here). The value is low-stakes —
    persona memory persists across boundaries regardless (Spec 01/14).
    """
    return (now - last_activity_at) > idle_after
