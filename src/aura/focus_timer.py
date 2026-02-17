"""
Focus Timer (Pomodoro-style) for AURA

Features:
- State machine with precise timing based on system time (monotonic) to avoid drift
- Work sessions (presets 25/50min) and automatic short breaks (5/10min)
- Start/Pause/Reset controls, quick preset buttons, and custom duration
- Session counter and basic stats
- Visual countdown Tk widget with color changes
- Optional sound and desktop notification on period end
- Integration hooks to suppress distraction notifications and mark break periods as allowed

Integration contract:
- Pass optional callbacks:
  - on_break_start(seconds): called when entering break, app should suppress distractions
  - on_break_end(): called when break ends, app should resume strict tracking
  - notify(title, message): to trigger desktop notification (uses AURA notifier if not provided)
- The widget does not own the mainloop; call .pack()/place()/grid() in the host UI.

Accuracy:
- Uses time.monotonic() snapshots to compute remaining time, not per-tick counters.
- Tick cadence defaults to 200ms for responsive UI while using monotonic deltas for correctness.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import tkinter as tk
from tkinter import ttk

try:
    from . import notifier as base_notifier
except Exception:  # pragma: no cover
    base_notifier = None  # type: ignore


# ---------------------------- Configuration ----------------------------


@dataclass
class FocusTimerConfig:
    work_minutes_default: int = 25
    work_minutes_alt: int = 50
    break_minutes_short: int = 5
    break_minutes_alt: int = 10
    tick_ms: int = 200  # UI refresh cadence
    enable_sound: bool = True


# ---------------------------- Core State Machine ----------------------------


class FocusTimer:
    """Pomodoro-style timer with precise timing and clear state transitions.

    States:
        IDLE -> WORKING -> BREAK -> (repeat)
        Transitions via start/pause/reset or natural completion.
    """

    def __init__(
        self,
        cfg: Optional[FocusTimerConfig] = None,
        on_break_start: Optional[Callable[[int], None]] = None,
        on_break_end: Optional[Callable[[], None]] = None,
        notify: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.cfg = cfg or FocusTimerConfig()
        self.on_break_start = on_break_start
        self.on_break_end = on_break_end
        self._notify = notify

        # Public stats
        self.completed_sessions: int = 0

        # Internal state
        self._state: str = "IDLE"  # IDLE | WORKING | BREAK | PAUSED
        self._work_target_s: int = self.cfg.work_minutes_default * 60
        self._break_target_s: int = self.cfg.break_minutes_short * 60
        self._started_mono: Optional[float] = None  # monotonic at (re)start
        self._paused_remaining_s: Optional[int] = None
        self._lock = threading.Lock()

        # Ticker thread control
        self._stop_event = threading.Event()
        self._ticker: Optional[threading.Thread] = None
        self._tick_callbacks: list[Callable[[int, int, str], None]] = []

    # ---------- Public API ----------
    def start_work(self, minutes: Optional[int] = None) -> None:
        with self._lock:
            if minutes is not None and minutes > 0:
                self._work_target_s = int(minutes * 60)
            # Start (or resume) work
            self._state = "WORKING"
            now = time.monotonic()
            if self._paused_remaining_s is not None:
                self._started_mono = now - (self._work_target_s - self._paused_remaining_s)
            else:
                self._started_mono = now
            self._paused_remaining_s = None
            self._ensure_ticker()

    def start_break(self, minutes: Optional[int] = None) -> None:
        with self._lock:
            if minutes is not None and minutes > 0:
                self._break_target_s = int(minutes * 60)
            self._state = "BREAK"
            self._started_mono = time.monotonic()
            self._paused_remaining_s = None
            cb = self.on_break_start
        if cb:
            try:
                cb(self._break_target_s)
            except Exception:
                pass
        self._ensure_ticker()

    def pause(self) -> None:
        with self._lock:
            if self._state in {"WORKING", "BREAK"} and self._started_mono is not None:
                remaining = self._remaining_locked()
                self._paused_remaining_s = remaining
                self._started_mono = None
                self._state = "PAUSED"

    def reset(self) -> None:
        with self._lock:
            self._state = "IDLE"
            self._started_mono = None
            self._paused_remaining_s = None

    def quick_preset(self, minutes: int) -> None:
        self.start_work(minutes)

    def on_tick(self, cb: Callable[[int, int, str], None]) -> None:
        """Register a tick callback receiving (remaining_s, total_s, state)."""
        self._tick_callbacks.append(cb)

    def get_state(self) -> Tuple[str, int, int]:
        """Return (state, remaining_s, total_s)."""
        with self._lock:
            if self._state == "WORKING":
                total = self._work_target_s
                rem = self._remaining_locked()
            elif self._state == "BREAK":
                total = self._break_target_s
                rem = self._remaining_locked()
            elif self._state == "PAUSED":
                if self._paused_remaining_s is not None:
                    if self._started_mono is None:
                        # paused
                        rem = self._paused_remaining_s
                    else:
                        rem = self._remaining_locked()
                else:
                    rem = 0
                total = self._work_target_s if self._work_target_s else 0
            else:
                total = self._work_target_s
                rem = total
            return (self._state, max(0, int(rem)), int(total))

    # ---------- Internals ----------
    def _ensure_ticker(self) -> None:
        if self._ticker is None or not self._ticker.is_alive():
            self._stop_event.clear()
            self._ticker = threading.Thread(target=self._run, daemon=True)
            self._ticker.start()

    def _run(self) -> None:
        tick_interval = max(0.05, self.cfg.tick_ms / 1000.0)
        while not self._stop_event.is_set():
            state, rem, total = self.get_state()
            # Fire ticks to listeners
            for cb in list(self._tick_callbacks):
                try:
                    cb(rem, total, state)
                except Exception:
                    pass
            # State transitions on completion
            if state in {"WORKING", "BREAK"} and rem <= 0:
                if state == "WORKING":
                    self.completed_sessions += 1
                    self._notify_end("Work complete", "Time for a short break!")
                    self.start_break(None)  # use configured break target
                else:  # BREAK
                    self._notify_end("Break over", "Back to focus now.")
                    cb = self.on_break_end
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
                    # Move to idle after break; allow user to start next work
                    with self._lock:
                        self._state = "IDLE"
                        self._started_mono = None
                        self._paused_remaining_s = None
            time.sleep(tick_interval)

    def _remaining_locked(self) -> int:
        assert self._state in {"WORKING", "BREAK", "PAUSED"}
        if self._state == "WORKING":
            total = self._work_target_s
        elif self._state == "BREAK":
            total = self._break_target_s
        else:
            total = self._work_target_s
        if self._started_mono is None:
            # Paused case uses paused_remaining if present
            return int(self._paused_remaining_s or total)
        elapsed = time.monotonic() - self._started_mono
        remaining = int(round(total - elapsed))
        return max(0, remaining)

    def _notify_end(self, title: str, message: str) -> None:
        # Prefer injected notifier; otherwise use base notifier if available
        try:
            if self._notify is not None:
                self._notify(title, message)
                return
            if base_notifier is not None:
                base_notifier.notify(title, message, timeout=6)
        except Exception:
            pass


# ---------------------------- Tkinter Widget ----------------------------


class FocusTimerWidget(ttk.Frame):
    """Visual control for FocusTimer: countdown label, buttons, and presets."""

    def __init__(
        self,
        master: tk.Misc,
        timer: FocusTimer,
        *,
        show_stats: bool = True,
    ) -> None:
        super().__init__(master, padding=8)
        self.timer = timer
        self.show_stats = show_stats

        # UI elements
        self.display = tk.Label(self, text="00:00", font=("Consolas", 20, "bold"), fg="#ffffff", bg="#126e00")
        self.display.pack(fill=tk.X)

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Start", command=self._start).pack(side=tk.LEFT)
        ttk.Button(btns, text="Pause", command=self._pause).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Reset", command=self._reset).pack(side=tk.LEFT, padx=(6, 0))

        presets = ttk.Frame(self)
        presets.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(presets, text="25 min", command=lambda: self.timer.start_work(25)).pack(side=tk.LEFT)
        ttk.Button(presets, text="50 min", command=lambda: self.timer.start_work(50)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(presets, text="5 min break", command=lambda: self.timer.start_break(5)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(presets, text="10 min break", command=lambda: self.timer.start_break(10)).pack(side=tk.LEFT, padx=(6, 0))

        custom = ttk.Frame(self)
        custom.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(custom, text="Custom (min):").pack(side=tk.LEFT)
        self.custom_var = tk.StringVar(value=str(self.timer.cfg.work_minutes_default))
        self.custom_entry = ttk.Entry(custom, textvariable=self.custom_var, width=5)
        self.custom_entry.pack(side=tk.LEFT, padx=(4, 6))
        ttk.Button(custom, text="Start", command=self._start_custom).pack(side=tk.LEFT)

        if self.show_stats:
            self.stats_lbl = ttk.Label(self, text="Sessions: 0")
            self.stats_lbl.pack(anchor="w", pady=(6, 0))
        else:
            self.stats_lbl = None

        # Register for timer ticks to update UI
        self.timer.on_tick(self._on_tick)

    # ---------- UI callbacks ----------
    def _start(self) -> None:
        try:
            self.timer.start_work(None)
        except Exception:
            pass

    def _pause(self) -> None:
        try:
            self.timer.pause()
        except Exception:
            pass

    def _reset(self) -> None:
        try:
            self.timer.reset()
            self._render(remaining=self.timer.cfg.work_minutes_default * 60, total=self.timer.cfg.work_minutes_default * 60, state="IDLE")
        except Exception:
            pass

    def _start_custom(self) -> None:
        try:
            mins = int(self.custom_var.get())
            if mins <= 0:
                return
            self.timer.start_work(mins)
        except Exception:
            pass

    # ---------- Rendering ----------
    def _on_tick(self, remaining_s: int, total_s: int, state: str) -> None:
        # ensure run on Tk thread
        try:
            self.after(0, lambda: self._render(remaining_s, total_s, state))
        except Exception:
            pass

    def _render(self, remaining: int, total: int, state: str) -> None:
        mm = max(0, remaining) // 60
        ss = max(0, remaining) % 60
        self.display.config(text=f"{mm:02d}:{ss:02d}")

        # Color logic
        frac = 1.0 if total <= 0 else (remaining / max(1, total))
        if state == "WORKING":
            # green -> orange -> red as time elapses
            if frac > 0.5:
                bg = "#126e00"  # green
            elif frac > 0.2:
                bg = "#9b6f0a"  # orange
            else:
                bg = "#b33a3a"  # red
        elif state == "BREAK":
            bg = "#0a5b9b"  # blue for break
        elif state == "PAUSED":
            bg = "#666666"
        else:
            bg = "#126e00"
        try:
            self.display.config(bg=bg)
        except Exception:
            pass

        if self.stats_lbl is not None:
            self.stats_lbl.config(text=f"Sessions: {self.timer.completed_sessions}")


__all__ = [
    "FocusTimerConfig",
    "FocusTimer",
    "FocusTimerWidget",
]
