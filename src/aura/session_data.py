
"""
In-memory session data management for AURA.

Tracks a timeline of focus/unfocus events, app usage, and precise durations.
Optimized for in-memory operations with pause/resume handling and no persistence.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional, List, Dict, Tuple


class FocusState(str, Enum):
    FOCUSED = "focused"
    UNFOCUSED = "unfocused"


@dataclass
class TimelineEvent:
    ts: float
    state: FocusState
    app: Optional[str] = None  # foreground application (e.g., "chrome.exe")
    title: Optional[str] = None  # window title or short context


@dataclass
class UsageBucket:
    focused_s: float = 0.0
    unfocused_s: float = 0.0


@dataclass
class SessionSummary:
    focused_s: float
    unfocused_s: float
    total_s: float
    app_usage: dict[str, UsageBucket]


def _new_timeline() -> List["TimelineEvent"]:
    return []


def _new_app_usage() -> Dict[str, "UsageBucket"]:
    return {}


@dataclass
class SessionData:
    """In-memory session store and accumulator.

    Contract:
    - add_activity(ts, is_focused, app, title): record a state sample at time ts.
    - tick(now): advance durations using last known state up to 'now'.
    - pause()/resume(): stop and continue accumulation without losing last state.
    - reset(): clear all data and restart timing anchor.
    - get_stats(): returns focused/unfocused totals and per-app usage.
    - get_pie_data(): returns labels and sizes suitable for a pie chart.

    Edge cases handled:
    - Minimization: sampling gaps are bridged by tick(now); durations accrue based on last state.
    - Out-of-order samples: if ts <= last_ts, we clamp to last_ts to avoid negative deltas.
    - Rapid state flips: accounted by processing deltas between consecutive events.
    """

    # timeline of events (append-only)
    timeline: List[TimelineEvent] = field(default_factory=_new_timeline)

    # per-app accumulators
    app_usage: Dict[str, UsageBucket] = field(default_factory=_new_app_usage)

    # totals
    focused_s: float = 0.0
    unfocused_s: float = 0.0

    # clocking
    _start_ts: float = field(default_factory=time)
    _last_ts: float = field(default_factory=time)
    _last_state: FocusState = FocusState.UNFOCUSED
    _last_app: Optional[str] = None
    _last_title: Optional[str] = None

    # paused state
    _paused: bool = False

    def add_activity(
        self,
        is_focused: bool,
        app: Optional[str] = None,
        title: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        """Record a new activity sample and account elapsed time since the previous one.

        If paused, we only update the last markers and append the event, without accruing time.
        """
        now = ts if ts is not None else time()
        # Normalize ordering
        if now < self._last_ts:
            now = self._last_ts

        # Accrue time up to 'now' based on last known state
        self._accrue_until(now)

        # Update last known markers and timeline
        state = FocusState.FOCUSED if is_focused else FocusState.UNFOCUSED
        self._last_state = state
        self._last_app = app
        self._last_title = title
        self._last_ts = now
        self.timeline.append(TimelineEvent(ts=now, state=state, app=app, title=title))

    def tick(self, now: Optional[float] = None) -> None:
        """Advance accumulation up to 'now' using last known state.

        Call this from a scheduler (e.g., every 0.5-1s) to keep totals precise,
        especially across UI minimization or when no new events arrive.
        """
        self._accrue_until(now if now is not None else time())

    def pause(self) -> None:
        """Pause accumulation; preserves last state without accruing during pause."""
        if not self._paused:
            self.tick()
            self._paused = True

    def resume(self) -> None:
        """Resume accumulation, resetting the last timestamp anchor to now."""
        if self._paused:
            self._last_ts = time()
            self._paused = False

    def reset(self) -> None:
        """Reset all in-memory data for a fresh session."""
        self.timeline.clear()
        self.app_usage.clear()
        self.focused_s = 0.0
        self.unfocused_s = 0.0
        self._start_ts = time()
        self._last_ts = self._start_ts
        self._last_state = FocusState.UNFOCUSED
        self._last_app = None
        self._last_title = None
        self._paused = False

    def get_stats(self) -> SessionSummary:
        """Return current accumulated stats and per-app usage map."""
        total = self.focused_s + self.unfocused_s
        return SessionSummary(
            focused_s=self.focused_s,
            unfocused_s=self.unfocused_s,
            total_s=total,
            app_usage=self.app_usage.copy(),
        )

    def get_pie_data(self) -> Tuple[List[str], List[float]]:
        """Return labels and sizes arrays for pie chart: [Focused, Unfocused]."""
        return ["Focused", "Unfocused"], [self.focused_s, self.unfocused_s]

    # Internal helpers

    def _accrue_until(self, now: float) -> None:
        if now <= self._last_ts:
            return
        delta = now - self._last_ts
        if delta <= 0:
            return

        if self._paused:
            # While paused, don't accrue but move the anchor to avoid backlogs.
            self._last_ts = now
            return

        # Add to totals
        if self._last_state == FocusState.FOCUSED:
            self.focused_s += delta
        else:
            self.unfocused_s += delta

        # Per-app bucket allocation if app known
        app_name = self._last_app
        if app_name:
            bucket = self.app_usage.get(app_name)
            if bucket is None:
                bucket = UsageBucket()
                self.app_usage[app_name] = bucket
            if self._last_state == FocusState.FOCUSED:
                bucket.focused_s += delta
            else:
                bucket.unfocused_s += delta

        # move anchor
        self._last_ts = now
