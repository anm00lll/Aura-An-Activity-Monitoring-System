from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Any
import sys
import platform
import ctypes

import pygetwindow as gw  # type: ignore[import-not-found]
try:
    from pynput import keyboard, mouse  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional on some versions
    keyboard = None  # type: ignore[assignment]
    mouse = None  # type: ignore[assignment]

from .session import Session
from . import notifier as aura_notifier


@dataclass
class TrackerStatus:
    window_title: str = ""
    is_focused: bool = False
    reason: str = "focused"  # "focused" | "idle" | "distracted"


class FocusTracker:
    """
    Periodically polls the active window and monitors input activity.
    Determines focused/unfocused state and updates the Session.
    """

    def __init__(
        self,
        session: Session,
        allowed_keywords: List[str] | None = None,
        poll_interval: float = 1.0,
        idle_threshold: int = 60,
        unfocused_notify_after: int = 10,
        on_update: Optional[Callable[[TrackerStatus, Session], None]] = None,
    ) -> None:
        self.session = session
        self.allowed_keywords = [k.strip().lower() for k in (allowed_keywords or []) if k.strip()]
        self.poll_interval = poll_interval
        self.idle_threshold = idle_threshold
        self.unfocused_notify_after = unfocused_notify_after
        self.on_update = on_update

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_input_ts = time.time()
        self._status = TrackerStatus()
        self._unfocused_streak = 0
        self._notified = False

        # Input listeners (disabled on Python 3.13+ due to pynput incompatibility)
        self._kb_listener = None
        self._ms_listener = None
        self._use_pynput = (keyboard is not None and mouse is not None and sys.version_info < (3, 13))
        if self._use_pynput:
            try:
                self._kb_listener = keyboard.Listener(on_press=self._on_input, on_release=self._on_input)  # type: ignore[no-untyped-call]
                if mouse is not None:  # extra guard
                    self._ms_listener = mouse.Listener(
                        on_move=self._on_mouse_move, on_click=self._on_mouse_click, on_scroll=self._on_input  # type: ignore[no-untyped-call]
                    )
            except Exception:
                self._kb_listener = None
                self._ms_listener = None

    def _on_input(self, *args: Any, **kwargs: Any) -> None:
        self._last_input_ts = time.time()

    def _on_mouse_move(self, x: float, y: float) -> None:
        self._on_input()

    def _on_mouse_click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        self._on_input()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        # Start listeners if available
        if self._kb_listener is not None:
            try:
                self._kb_listener.start()
            except Exception:
                self._kb_listener = None
        if self._ms_listener is not None:
            try:
                self._ms_listener.start()
            except Exception:
                self._ms_listener = None
        self._thread = threading.Thread(target=self._run, name="AURA-FocusTracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._kb_listener is not None:
                self._kb_listener.stop()
            if self._ms_listener is not None:
                self._ms_listener.stop()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @property
    def status(self) -> TrackerStatus:
        return self._status

    def set_allowed_keywords(self, keywords: List[str]) -> None:
        self.allowed_keywords = [k.strip().lower() for k in keywords if k.strip()]

    def _get_active_title(self) -> str:
        try:
            win = gw.getActiveWindow()  # type: ignore[call-arg]
            if win is None:
                return ""
            # Some windows may have None title
            title = getattr(win, "title", "") or ""
            return str(title)
        except Exception:
            return ""

    def _get_idle_seconds(self, now: float) -> float:
        # Prefer Windows API if available (works on Python 3.13+)
        if platform.system() == "Windows":
            try:
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

                last_input_info = LASTINPUTINFO()
                last_input_info.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input_info)):
                    tick_count = ctypes.windll.kernel32.GetTickCount()
                    idle_ms = tick_count - last_input_info.dwTime
                    return max(0.0, idle_ms / 1000.0)
            except Exception:
                pass
        # Fallback: estimate based on last input time (updated by pynput if active)
        return max(0.0, now - self._last_input_ts)

    def _classify(self, title: str, now: float) -> tuple[bool, str]:
        # Idle check
        idle_for = self._get_idle_seconds(now)
        if idle_for >= self.idle_threshold:
            return False, "idle"

        # Allowed keywords check
        title_l = title.lower()
        if self.allowed_keywords and any(k in title_l for k in self.allowed_keywords):
            return True, "focused"
        elif not self.allowed_keywords:
            # If no keywords configured, treat everything as focused
            return True, "focused"
        else:
            return False, "distracted"

    def _run(self) -> None:
        while not self._stop_event.is_set():
            start = time.time()
            title = self._get_active_title()
            now = time.time()
            is_focused, reason = self._classify(title, now)

            # Update session
            self.session.tick(is_focused=is_focused, now=now)

            # Track streaks for notifications
            if is_focused:
                self._unfocused_streak = 0
                self._notified = False
            else:
                self._unfocused_streak += 1
                if reason == "distracted" and self._unfocused_streak >= self.unfocused_notify_after and not self._notified:
                    try:
                        aura_notifier.notify("AURA: Stay on task", "You seem distracted. Refocus on your task.")
                    except Exception as e:  # Extra safety if symbol is shadowed
                        print(f"AURA notification error: {e}")
                    self._notified = True

            # Publish status
            self._status = TrackerStatus(window_title=title, is_focused=is_focused, reason=reason)
            if self.on_update:
                try:
                    self.on_update(self._status, self.session)
                except Exception:
                    pass

            # Sleep remainder of poll interval
            elapsed = time.time() - start
            to_sleep = max(0.0, self.poll_interval - elapsed)
            self._stop_event.wait(timeout=to_sleep)
