"""T12 (§2.1 / D-25-5 / D-25-6 / D-25-X-t12-window-location) — runtime
turn-loop rolling fallback-rate window.

Exercises ``ConversationLoop._update_fallback_window`` in isolation (via
``__new__`` so the heavy constructor deps aren't needed — the method only
touches ``_fallback_window`` + ``_fallback_alerting``). Pins the D-25-5
contract: strict >30% (≥4/10) entry with a min-sample guard, ≤20% (≤2/10)
hysteresis clear, and the 3/10 no-flap boundary.
"""

from __future__ import annotations

from collections import deque

from persona_runtime.loop import ConversationLoop


def _fresh_loop() -> ConversationLoop:
    loop = ConversationLoop.__new__(ConversationLoop)
    loop._fallback_window = deque(maxlen=10)  # type: ignore[attr-defined]  # noqa: SLF001
    loop._fallback_alerting = False  # type: ignore[attr-defined]  # noqa: SLF001
    return loop


def _push(loop: ConversationLoop, engaged: bool) -> bool:
    return loop._update_fallback_window(  # noqa: SLF001
        engaged=engaged, conversation_id="c", provider="nvidia", model="m"
    )


def test_min_sample_guard_no_alert_under_5_turns() -> None:
    loop = _fresh_loop()
    # 100% fallback but only 2 samples → guarded (need ≥5 turns), no alert.
    assert _push(loop, True) is False
    assert _push(loop, True) is False
    assert _push(loop, True) is False
    assert _push(loop, True) is False  # 4 turns, still <5 → guarded


def test_fires_when_rate_strictly_exceeds_30pct() -> None:
    loop = _fresh_loop()
    # 5 clean, then fallbacks until 3/9 = 33% > 30% with n≥5 and count≥2.
    for _ in range(5):
        assert _push(loop, False) is False  # 0/1..0/5
    assert _push(loop, True) is False  # 1/6 = 17%
    assert _push(loop, True) is False  # 2/7 = 29%, not >30
    assert _push(loop, True) is True  # 3/8 = 37.5% > 30 → fires
    assert loop._fallback_alerting is True  # noqa: SLF001


def test_three_of_ten_does_not_flap() -> None:
    loop = _fresh_loop()
    # Fallbacks at the END so no earlier prefix transiently exceeds 30%:
    # n=8 → 1/8, n=9 → 2/9 (22%), n=10 → 3/10 (30%, NOT strictly >30%).
    pattern = [False] * 7 + [True] * 3
    alerts = [_push(loop, e) for e in pattern]
    assert alerts[-1] is False
    assert loop._fallback_alerting is False  # noqa: SLF001


def test_fires_then_clears_with_hysteresis() -> None:
    loop = _fresh_loop()
    # Full window at 4/10 = 40% → alerting.
    for e in [True, True, True, True, False, False, False, False, False, False]:
        _push(loop, e)
    assert loop._fallback_alerting is True  # noqa: SLF001
    # Push clean turns; alert persists through the >20% band and clears at ≤2/10.
    cleared = False
    for _ in range(10):
        if _push(loop, False) is False:
            cleared = True
            break
    assert cleared is True
    assert loop._fallback_alerting is False  # noqa: SLF001


def test_alert_state_is_stable_while_above_threshold() -> None:
    loop = _fresh_loop()
    for e in [True, True, True, True, False, False, False, False, False, False]:
        _push(loop, e)
    assert loop._fallback_alerting is True  # noqa: SLF001
    # Another fallback keeps it alerting (edge-triggered: no off/on flip).
    assert _push(loop, True) is True
    assert loop._fallback_alerting is True  # noqa: SLF001
