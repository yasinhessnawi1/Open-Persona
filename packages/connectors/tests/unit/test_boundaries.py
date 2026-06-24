"""Conversation-boundary logic — the pure idle predicate (Spec C1 T8, D-C1-3, §3).

`/new` and the idle-timeout are the only operations that END a persona's
conversation (switching suspends, never ends). The transactional ops live in the
infra store; this is the pure, deterministic predicate (``now``/``idle_after``
injected) the idle sweep applies — per-persona-per-channel. Owned surface, api-free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from persona_connectors.domain.boundaries import is_idle_expired

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
_TIMEOUT = timedelta(minutes=30)


def test_recent_activity_is_not_expired() -> None:
    last = _NOW - timedelta(minutes=5)
    assert is_idle_expired(last, now=_NOW, idle_after=_TIMEOUT) is False


def test_gap_past_the_timeout_is_expired() -> None:
    last = _NOW - timedelta(minutes=31)
    assert is_idle_expired(last, now=_NOW, idle_after=_TIMEOUT) is True


def test_exactly_at_the_timeout_is_not_yet_expired() -> None:
    """Boundary: a gap of exactly the timeout is still within (strict >)."""
    last = _NOW - _TIMEOUT
    assert is_idle_expired(last, now=_NOW, idle_after=_TIMEOUT) is False


def test_just_past_the_timeout_is_expired() -> None:
    last = _NOW - _TIMEOUT - timedelta(seconds=1)
    assert is_idle_expired(last, now=_NOW, idle_after=_TIMEOUT) is True
