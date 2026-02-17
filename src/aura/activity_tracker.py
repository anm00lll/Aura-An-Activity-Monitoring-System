from __future__ import annotations

"""
ActivityTracker: multi-signal focus detection with millisecond timestamps and event callbacks.

Usage example:

    from aura.activity_tracker import ActivityTracker

    def on_event(evt):
        print(evt.type, evt.ts_ms, evt.payload)

    tracker = ActivityTracker(allowed_keywords=["code", "visual studio", "chrome"],
                               app_timeouts={"chrome.exe": {"idle_timeout_s": 420, "think_timeout_s": 120}})
    tracker.add_listener(on_event)
    tracker.start()
    # ... later ...
    tracker.stop()
"""

import ctypes
import threading
import time
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any, Callable, Dict, Optional

import psutil  # type: ignore[import-not-found]

try:
    import mss  # type: ignore
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    mss = None  # type: ignore
    Image = None  # type: ignore

try:
    import pygetwindow as gw  # type: ignore
except Exception:  # pragma: no cover
    gw = None  # type: ignore


# ------------------------ Windows API helpers ------------------------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds() -> float:
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            tick = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, (tick - lii.dwTime) / 1000.0)
    except Exception:
        pass
    return 0.0


def _get_window_text(hwnd: int, max_len: int = 1024) -> str:
    try:
        buf = ctypes.create_unicode_buffer(max_len)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, max_len)
        return buf.value
    except Exception:
        return ""


def get_foreground_app() -> tuple[str, str]:
    """Return (window_title, process_name) using Win32 APIs with pygetwindow fallback."""
    title = ""
    proc_name = ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            title = _get_window_text(hwnd)
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                p = psutil.Process(int(pid.value))
                proc_name = p.name()
        elif gw is not None:
            # Fallback via pygetwindow when hwnd not retrievable
            w = gw.getActiveWindow()
            if w is not None:
                title = str(getattr(w, "title", "") or "")
                proc_name = ""
    except Exception:
        pass
    return title or "", proc_name or ""


def screen_hash(monitor: Optional[Dict[str, int]] = None, downscale: int = 4) -> str:
    """Return a robust hash of the current screen content; combine all monitors if none specified."""
    if mss is None or Image is None:
        return ""
    try:
        with mss.mss() as sct:  # type: ignore
            if monitor is not None:
                mons = [monitor]
            else:
                mons = list(sct.monitors[1:]) or [sct.monitors[0]]  # type: ignore
            # mons is a list of monitor dicts from mss; keep as-is
            dig = blake2b(digest_size=16)
            for mon in mons:
                img = sct.grab(mon)  # type: ignore
                im = Image.frombytes('RGB', img.size, img.rgb)  # type: ignore
                w = max(1, im.width // downscale)
                h = max(1, im.height // downscale)
                im_small = im.resize((w, h))  # type: ignore
                dig.update(im_small.tobytes())
            return dig.hexdigest()
    except Exception:
        return ""


def get_cursor_pos() -> tuple[int, int]:
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    return (0, 0)


# ------------------------ Data structures ------------------------

@dataclass
class ActivityEvent:
    ts_ms: int
    type: str  # "app_switch" | "focus_change" | "input" | "idle_state" | "screen_change"
    payload: Dict[str, Any]


@dataclass
class FocusState:
    focused: bool
    reason: str  # "focused" | "idle" | "distracted" | "reading"
    window_title: str
    app_name: str


# ------------------------ ActivityTracker ------------------------

class ActivityTracker:
    """
    Multi-signal, millisecond-precision activity tracker with event callbacks.

    Signals:
    - Foreground window title + app name (pygetwindow + Win32)
    - Idle seconds (Win32 GetLastInputInfo)
    - Screen content change hashing (mss + Pillow)
    - Input intensity heuristics (via idle deltas & change rates)

    Heuristics for idle vs reading vs thinking pauses:
    - If idle < app-specific think_timeout and screen hash changes slowly, classify as "reading".
    - If idle < app-specific think_timeout and window unchanged, but short bursty inputs appear, still "focused".
    - If idle > app-specific idle_timeout, classify as "idle".
    - If foreground app/title not in allowed keywords, classify as "distracted".
    """

    def __init__(
        self,
        allowed_keywords: list[str] | None = None,
        poll_interval_ms: int = 500,
        default_idle_timeout_s: int = 300,
        default_think_timeout_s: int = 90,
        app_timeouts: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> None:
        self.allowed_keywords = [k.strip().lower() for k in (allowed_keywords or []) if k.strip()]
        self.poll_interval_ms = max(100, poll_interval_ms)
        self.default_idle_timeout_s = default_idle_timeout_s
        self.default_think_timeout_s = default_think_timeout_s
        self.app_timeouts = app_timeouts or {}

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listeners: list[Callable[[ActivityEvent], None]] = []

        self._last_title = ""
        self._last_app = ""
        self._last_hash = ""
        self._last_screen_change_ms = 0
        self._last_focus_state: Optional[FocusState] = None
        self._last_idle_s: Optional[float] = None
        self._last_cursor_xy: Optional[tuple[int, int]] = None

    # --------------- Public API ---------------
    def add_listener(self, fn: Callable[[ActivityEvent], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[ActivityEvent], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="AURA-ActivityTracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # --------------- Internal ---------------
    def _emit(self, type: str, payload: Dict[str, Any]) -> None:
        evt = ActivityEvent(ts_ms=self._now_ms(), type=type, payload=payload)
        for fn in list(self._listeners):
            try:
                fn(evt)
            except Exception:
                pass

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _get_timeouts(self, app_name: str) -> tuple[int, int]:
        conf = self.app_timeouts.get(app_name.lower()) or {}
        idle_t = int(conf.get("idle_timeout_s", self.default_idle_timeout_s))
        think_t = int(conf.get("think_timeout_s", self.default_think_timeout_s))
        return idle_t, think_t

    def _estimate_focus(self, title: str, app: str, idle_s: float, hash_changed: bool, cursor_move_px: int) -> FocusState:
        idle_t, think_t = self._get_timeouts(app)
        title_l = (title or "").lower()
        allowed = (not self.allowed_keywords) or any(k in title_l or k in (app or "").lower() for k in self.allowed_keywords)

        # System/lock screen heuristic: no title/process and long idle
        if not title and not app and idle_s > 5:
            return FocusState(False, "system", title, app)

        if idle_s >= idle_t:
            return FocusState(False, "idle", title, app)

        # Reading heuristic: little/no input, but occasional screen hash changes
        if idle_s >= 3 and idle_s < think_t and hash_changed and cursor_move_px < 5:
            return FocusState(True, "reading", title, app)

        # Focus heuristic: allowed title/app
        if allowed or cursor_move_px >= 20:
            return FocusState(True, "focused", title, app)

        return FocusState(False, "distracted", title, app)

    def _run(self) -> None:
        while not self._stop.is_set():
            loop_start_ms = self._now_ms()

            title, app = get_foreground_app()
            if title != self._last_title or app != self._last_app:
                self._emit("app_switch", {"title": title, "app": app, "prev_title": self._last_title, "prev_app": self._last_app})
                self._last_title, self._last_app = title, app

            idle_s = get_idle_seconds()
            self._emit("idle_state", {"idle_seconds": idle_s})
            # Input detection based on idle reset and cursor movement
            cx, cy = get_cursor_pos()
            move_px = 0
            if self._last_cursor_xy is not None:
                move_px = abs(cx - self._last_cursor_xy[0]) + abs(cy - self._last_cursor_xy[1])
            self._last_cursor_xy = (cx, cy)

            if self._last_idle_s is not None and idle_s < self._last_idle_s:
                self._emit("input", {"delta_idle": self._last_idle_s - idle_s, "cursor_move_px": move_px})
            self._last_idle_s = idle_s

            # Screen change detection (sample at most once per second)
            hash_now = self._last_hash
            changed = False
            if self._now_ms() - self._last_screen_change_ms >= 1000:
                hash_now = screen_hash()
                if hash_now and self._last_hash and hash_now != self._last_hash:
                    changed = True
                    self._emit("screen_change", {"hash": hash_now})
                self._last_hash = hash_now
                self._last_screen_change_ms = self._now_ms()

            state = self._estimate_focus(title, app, idle_s, changed, move_px)
            if self._last_focus_state is None or (state.focused != self._last_focus_state.focused or state.reason != self._last_focus_state.reason):
                self._emit("focus_change", {
                    "focused": state.focused,
                    "reason": state.reason,
                    "title": state.window_title,
                    "app": state.app_name,
                })
                self._last_focus_state = state

            # Sleep remaining time
            elapsed_ms = self._now_ms() - loop_start_ms
            to_sleep = max(0, self.poll_interval_ms - elapsed_ms)
            self._stop.wait(to_sleep / 1000.0)


__all__ = [
    "ActivityTracker",
    "ActivityEvent",
    "FocusState",
    "get_idle_seconds",
    "get_foreground_app",
    "screen_hash",
]
