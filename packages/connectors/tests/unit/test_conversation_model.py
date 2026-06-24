"""The parallel-conversation decision rules — pure logic (Spec C1 T6, §3).

The agentic-future linchpin, as a pure decision over the current channel state
(the transactional FOR UPDATE flip is the infra adapter's job). The §3 rules:

- naming a persona foregrounds it; if a *different* persona was active, that one is
  SUSPENDED (never ended) and the named one is resumed (if it has a live slot) or
  started fresh;
- re-naming the *active* persona is a NO-OP (never resets — the always-safe rule);
- switching never resets either conversation.

Owned surface — api-free.
"""

from __future__ import annotations

from persona_connectors.domain.conversation_model import NoOp, Switch, decide_foreground


def test_renaming_the_active_persona_is_a_noop() -> None:
    """The always-safe rule: naming the already-active persona never resets."""
    plan = decide_foreground(
        active_persona_id="astrid", named_persona_id="astrid", named_has_resumable_slot=True
    )
    assert isinstance(plan, NoOp)


def test_switch_from_active_suspends_current_and_resumes_named_with_a_slot() -> None:
    """Switch to a persona that has a suspended slot: suspend the current, RESUME the named."""
    plan = decide_foreground(
        active_persona_id="kai", named_persona_id="astrid", named_has_resumable_slot=True
    )
    assert isinstance(plan, Switch)
    assert plan.suspend_persona_id == "kai"  # the previously-active is suspended (not ended)
    assert plan.foreground_persona_id == "astrid"
    assert plan.resume is True  # resume the existing (intact) conversation


def test_switch_to_a_persona_without_a_slot_starts_fresh() -> None:
    """Switch to a never-before-seen persona: suspend the current, START a new conversation."""
    plan = decide_foreground(
        active_persona_id="kai", named_persona_id="astrid", named_has_resumable_slot=False
    )
    assert isinstance(plan, Switch)
    assert plan.suspend_persona_id == "kai"
    assert plan.foreground_persona_id == "astrid"
    assert plan.resume is False  # no live slot → start


def test_first_contact_no_active_persona_starts_the_named() -> None:
    """No active persona yet: foreground the named one (nothing to suspend)."""
    plan = decide_foreground(
        active_persona_id=None, named_persona_id="astrid", named_has_resumable_slot=False
    )
    assert isinstance(plan, Switch)
    assert plan.suspend_persona_id is None  # nothing was active
    assert plan.foreground_persona_id == "astrid"
    assert plan.resume is False


def test_resume_a_previously_suspended_persona_with_no_active() -> None:
    """Re-naming a persona whose slot is suspended (and nothing currently active) resumes it."""
    plan = decide_foreground(
        active_persona_id=None, named_persona_id="astrid", named_has_resumable_slot=True
    )
    assert isinstance(plan, Switch)
    assert plan.suspend_persona_id is None
    assert plan.foreground_persona_id == "astrid"
    assert plan.resume is True
