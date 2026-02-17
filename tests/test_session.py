import time

from aura.session import Session


def test_session_tick_accumulates_focus_and_unfocus():
    s = Session()
    base = time.time()
    s.last_tick = base

    s.tick(is_focused=True, now=base + 5)
    assert s.stats.focused_seconds == 5
    assert s.stats.unfocused_seconds == 0

    s.tick(is_focused=False, now=base + 10)
    assert s.stats.focused_seconds == 5
    assert s.stats.unfocused_seconds == 5
