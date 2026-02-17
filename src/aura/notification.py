from __future__ import annotations

"""
Smart, non-intrusive distraction notifications for AURA.

Features:
- Desktop notifications when distraction detected (via plyer with safe fallback)
- Escalating reminders for prolonged distractions
- Optional floating indicator window showing current focus state
- Configurable delays, frequency, and suppression during breaks
- Context-aware, positive, and actionable messages

Usage (with ActivityTracker + DistractionClassifier):

    from aura.notification import NotificationManager, NotificationSettings

    nm = NotificationManager(NotificationSettings())
    nm.start_indicator(root)  # optional: show floating indicator in the Tk UI

    # When focus/distraction changes:
    nm.on_focus_event(
        focused=not res.is_distracted,  # from classifier result
        category=res.category if res.is_distracted else None,
        reason=res.category if res.is_distracted else "focused",
        ts_ms=evt.ts_ms,
        title=title,
        app=app,
    )

"""

import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Any

try:
    import tkinter as tk
except Exception:  # pragma: no cover - optional at runtime
    tk = None  # type: ignore

from . import notifier as base_notifier


# ---------------------------- Settings & Severity ----------------------------


@dataclass
class NotificationSettings:
    # Timing
    distraction_delay_s: int = 10  # wait before first alert on a new distraction
    min_interval_s: int = 60       # minimum time between notifications
    refocus_quiet_s: int = 20      # grace period after refocus where we stay quiet
    escalate_after_s: Tuple[int, int, int] = (45, 120, 300)  # escalation thresholds

    # Behavior
    suppress_during_break: bool = True
    enable_indicator: bool = True

    # Indicator UI
    indicator_corner: str = "top-right"  # top-left | top-right | bottom-left | bottom-right
    indicator_width: int = 200
    indicator_height: int = 28
    indicator_alpha: float = 0.9
    color_focused: str = "#126e00"  # green-ish
    color_reading: str = "#0a5b9b"  # blue-ish
    color_distracted: str = "#b33a3a"  # red-ish
    color_idle: str = "#666666"


def _severity_for_category(category: Optional[str]) -> int:
    # 1=low, 2=med, 3=high
    if not category:
        return 1
    cat = category.lower()
    if cat in {"youtube_shorts", "gaming"}:
        return 3
    if cat in {"social", "entertainment", "communication_personal"}:
        return 2
    if cat in {"news"}:
        return 1
    return 1


# ---------------------------- Notification Manager ----------------------------


class NotificationManager:
    def __init__(self, settings: Optional[NotificationSettings] = None) -> None:
        self.cfg = settings or NotificationSettings()

        # State
        self._break_until_ms: int = 0
        self._last_notify_s: float = 0.0
        self._last_focus_change_s: float = 0.0
        self._current: Optional[str] = None  # current category if distracted
        self._current_started_s: float = 0.0

        # Indicator
        self._tk_parent: Optional[Any] = None
        self._indicator: Optional[Any] = None
        self._indicator_label: Optional[Any] = None
        self._indicator_bg: str = self.cfg.color_idle
        self._indicator_text: str = "Idle"

        # Lock for thread-safety
        self._lock = threading.Lock()

    # -------- Controls --------
    def set_break(self, seconds: int) -> None:
        self._break_until_ms = int(time.time() * 1000) + max(0, seconds) * 1000

    def set_break_active(self, active: bool, duration_s: Optional[int] = None) -> None:
        if active:
            self.set_break(duration_s or 300)
        else:
            self._break_until_ms = 0

    # -------- Public API --------
    def start_indicator(self, tk_parent: Any) -> None:
        if not self.cfg.enable_indicator or tk is None:
            return
        self._tk_parent = tk_parent
        if self._indicator is not None:
            return
        try:
            win: Any = tk.Toplevel(self._tk_parent)
            win.overrideredirect(True)
            win.wm_attributes("-topmost", True)  # type: ignore[arg-type]
            try:
                win.wm_attributes("-alpha", self.cfg.indicator_alpha)  # type: ignore[arg-type]
            except Exception:
                pass
            win.geometry(self._indicator_geometry())
            lbl: Any = tk.Label(win, text="Idle", fg="#ffffff", bg=self.cfg.color_idle, font=("Segoe UI", 9))
            lbl.pack(fill=tk.BOTH, expand=True)
            self._indicator = win
            self._indicator_label = lbl
        except Exception:
            self._indicator = None
            self._indicator_label = None

    def stop_indicator(self) -> None:
        if self._indicator is not None:
            try:
                self._indicator.destroy()
            except Exception:
                pass
        self._indicator = None
        self._indicator_label = None

    def on_focus_event(
        self,
        focused: bool,
        category: Optional[str],
        reason: str,
        ts_ms: int,
        title: str = "",
        app: str = "",
    ) -> None:
        """Call on every focus/distraction change.

        - When focused becomes True, we apply a quiet period to avoid spam.
        - When distracted, we consider delay, frequency, escalation, and severity.
        - Updates the floating indicator if enabled.
        """
        now_s = ts_ms / 1000.0
        with self._lock:
            # Indicator update
            self._update_indicator_async(focused, reason)

            # Break suppression
            if self.cfg.suppress_during_break and self._is_on_break(ts_ms):
                return

            # Refocus quiet window
            if focused:
                self._current = None
                self._current_started_s = 0.0
                self._last_focus_change_s = now_s
                return

            # If just refocused recently, avoid immediate notification
            if (now_s - self._last_focus_change_s) < self.cfg.refocus_quiet_s:
                return

            # Track or continue the current distraction category
            if self._current != category:
                self._current = category or "other"
                self._current_started_s = now_s

            distracted_duration = max(0.0, now_s - self._current_started_s)

            # First-notification delay
            if distracted_duration < self.cfg.distraction_delay_s:
                return

            # Frequency throttle
            if (now_s - self._last_notify_s) < self.cfg.min_interval_s:
                return

            # Escalation & severity
            level = 0
            for i, t in enumerate(self.cfg.escalate_after_s, start=1):
                if distracted_duration >= t:
                    level = i
            severity = _severity_for_category(self._current)

            n_title, n_msg, timeout = self._compose_message(
                category=self._current,
                title=title,
                app=app,
                duration_s=int(distracted_duration),
                level=level,
                severity=severity,
            )
            self._send_notification(n_title, n_msg, timeout=timeout)
            self._last_notify_s = now_s

    # -------- Internals --------
    def _is_on_break(self, now_ms: int) -> bool:
        return bool(self._break_until_ms) and now_ms < self._break_until_ms

    def _send_notification(self, title: str, message: str, timeout: int = 5) -> None:
        try:
            base_notifier.notify(title, message, timeout=timeout)
        except Exception:
            # As a last resort, print to console
            print(f"[AURA] {title}: {message}")

    def _compose_message(
        self,
        category: Optional[str],
        title: str,
        app: str,
        duration_s: int,
        level: int,
        severity: int,
    ) -> Tuple[str, str, int]:
        # Encouraging, context-aware, brief, actionable
        cat_txt = (category or "distraction").replace("_", " ")
        where = title or app or "this app"
        base = f"You drifted to {cat_txt} in {where}."

        if level >= 3 or severity >= 3:
            sugg = "Quick reset: close the tab, 3 deep breaths, then return to your task."
            n_title = "AURA: Let's refocus"
            timeout = 7
        elif level == 2 or severity == 2:
            sugg = "Try a 2‑minute pause, then get back to your main goal."
            n_title = "AURA: Nudge to refocus"
            timeout = 6
        else:
            sugg = "Small nudge: switch back to your task when ready."
            n_title = "AURA: Gentle reminder"
            timeout = 5

        msg = f"{base}\n{duration_s}s away. {sugg}"
        return n_title, msg, timeout

    def _indicator_geometry(self) -> str:
        w = self.cfg.indicator_width
        h = self.cfg.indicator_height
        x = 10
        y = 10
        if self._tk_parent is not None:
            try:
                self._tk_parent.update_idletasks()
                sw = self._tk_parent.winfo_screenwidth()
                sh = self._tk_parent.winfo_screenheight()
            except Exception:
                sw = 1920
                sh = 1080
        else:
            sw = 1920
            sh = 1080

        corner = (self.cfg.indicator_corner or "top-right").lower()
        if corner == "top-left":
            x, y = 10, 10
        elif corner == "top-right":
            x, y = max(0, sw - w - 10), 10
        elif corner == "bottom-left":
            x, y = 10, max(0, sh - h - 50)
        else:  # bottom-right
            x, y = max(0, sw - w - 10), max(0, sh - h - 50)
        return f"{w}x{h}+{x}+{y}"

    def _update_indicator_async(self, focused: bool, reason: str) -> None:
        if not self.cfg.enable_indicator or self._indicator_label is None or self._indicator is None:
            return

        def _apply() -> None:
            if focused:
                bg = self.cfg.color_focused
                txt = "Focused"
            else:
                # Reading = focused but different reason in upstream; handle common tags here
                if reason == "reading":
                    bg = self.cfg.color_reading
                    txt = "Reading"
                elif reason == "idle":
                    bg = self.cfg.color_idle
                    txt = "Idle"
                else:
                    bg = self.cfg.color_distracted
                    # Show a short category name if exists
                    txt = f"Distracted: {reason[:14]}" if reason else "Distracted"
            try:
                label: Any = self._indicator_label
                ind: Any = self._indicator
                label.config(text=txt, bg=bg)
                ind.config(bg=bg)
            except Exception:
                pass

        # Run on Tk thread
        try:
            assert self._tk_parent is not None
            self._tk_parent.after(0, _apply)
        except Exception:
            pass


__all__ = [
    "NotificationSettings",
    "NotificationManager",
]
