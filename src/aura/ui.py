from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Optional, Any, cast

from .activity_tracker import ActivityTracker, ActivityEvent
from .tracker import FocusTracker, TrackerStatus
from .session import Session
from .session_data import SessionData
from .plotter import show_pie_summary
from .classifier import DistractionClassifier
from .notification import NotificationManager, NotificationSettings

# Optional: system tray support with safe alias to satisfy type checker
try:
    import pystray as _pystray  # type: ignore
except Exception:  # pragma: no cover - optional at runtime
    _pystray = None  # type: ignore[assignment]

try:
    from PIL import Image as _PILImage, ImageDraw as _PILImageDraw  # Pillow is already in requirements
    Image = _PILImage  # type: ignore[assignment]
    ImageDraw = _PILImageDraw  # type: ignore[assignment]
except Exception:  # pragma: no cover - optional at runtime
    Image = cast(Any, None)
    ImageDraw = cast(Any, None)


def _format_hms(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class AuraMinimalUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AURA – Minimal UI")
        self.root.geometry("520x220")

        # Data and tracking
        self.session_data = SessionData()
        # Trackers
        self.tracker = None
        self.adv_tracker = None
        self._last_title = ""
        self._last_app = ""
        self._last_focused = False
        self._last_reason = "focused"
        self._tick_job = None
        self._running = False
        self.use_advanced = tk.BooleanVar(value=True)
        self.allowed_keywords: set[str] = set()
        # Classifier/notifications
        self.classifier = None
        self._last_cls = None
        self.notifier = None
        # Basic status cache and session
        self._basic_status = None
        self._basic_session = None

        # Tray
        self._tray_icon = None
        self._tray_thread = None
        self._tray_visible = False

        self._build_ui()
        self._bind_close()

    # ---------------- UI -----------------
    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Menu bar
        menubar = tk.Menu(self.root)
        session_menu = tk.Menu(menubar, tearoff=0)
        session_menu.add_command(label="Start Session", command=self.start_tracking)
        session_menu.add_command(label="Stop Session", command=self.stop_tracking)
        session_menu.add_separator()
        session_menu.add_command(label="Show Summary", command=self._show_summary_dialog)
        session_menu.add_separator()
        session_menu.add_command(label="Exit", command=self._hard_exit)
        menubar.add_cascade(label="Session", menu=session_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Set Focus Keywords", command=self._set_keywords)
        settings_menu.add_checkbutton(
            label="Use Advanced Tracker",
            variable=self.use_advanced,
            onvalue=True,
            offvalue=False,
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="Start Break (5 min)", command=lambda: self._start_break(5 * 60))
        settings_menu.add_command(label="Stop Break", command=self._stop_break)
        settings_menu.add_separator()
        settings_menu.add_command(label="Whitelist Current App", command=self._whitelist_current_app)
        settings_menu.add_command(label="Whitelist Current Domain", command=self._whitelist_current_domain)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)

        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        # Top row: Start/Stop + Status
        top = ttk.Frame(container)
        top.pack(fill=tk.X)

        self.start_btn = ttk.Button(top, text="Start Tracking", command=self.start_tracking, width=18)
        self.start_btn.pack(side=tk.LEFT)

        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop_tracking, state=tk.DISABLED, width=10)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.status_lbl = ttk.Label(top, text="Status: Idle")
        self.status_lbl.pack(side=tk.LEFT, padx=(16, 0))

        # Middle: Active app + focus state
        mid = ttk.Frame(container)
        mid.pack(fill=tk.X, pady=(12, 4))

        self.app_lbl = ttk.Label(mid, text="Active: —", justify=tk.LEFT)
        self.app_lbl.pack(anchor="w")

        self.focus_state_lbl = ttk.Label(mid, text="Focus: —")
        self.focus_state_lbl.pack(anchor="w", pady=(4, 0))

        # Timers
        bottom = ttk.Frame(container)
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.focus_time_lbl = ttk.Label(bottom, text="Focused: 00:00:00")
        self.focus_time_lbl.pack(anchor="w")

        self.unfocus_time_lbl = ttk.Label(bottom, text="Unfocused: 00:00:00")
        self.unfocus_time_lbl.pack(anchor="w")

        # Controls row 2
        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(12, 0))

        self.tray_btn = ttk.Button(controls, text="Minimize to Tray", command=self.minimize_to_tray, width=18)
        self.tray_btn.pack(side=tk.LEFT)

    def _bind_close(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------- Tracking ---------------
    def start_tracking(self) -> None:
        if self._running:
            return
        # Reset data
        self.session_data.reset()
        self._last_title = ""
        self._last_app = ""
        self._last_focused = False
        self._last_reason = "focused"
        # Prepare classifier and notifier
        self.classifier = DistractionClassifier()
        self._last_cls = None
        self.notifier = NotificationManager(NotificationSettings())
        try:
            # optional floating indicator
            self.notifier.start_indicator(self.root)
        except Exception:
            pass
        # Start tracker(s)
        if self.use_advanced.get():
            self.adv_tracker = ActivityTracker(
                allowed_keywords=sorted(self.allowed_keywords),
                poll_interval_ms=500,
                default_idle_timeout_s=300,
                default_think_timeout_s=90,
            )
            self.adv_tracker.add_listener(self._on_activity_event)
            self.adv_tracker.start()
        else:
            self._basic_session = Session()
            self.tracker = FocusTracker(
                session=self._basic_session,
                allowed_keywords=sorted(self.allowed_keywords),
                poll_interval=1.0,
                idle_threshold=60,
                unfocused_notify_after=10,
                on_update=self._on_tracker_update,
            )
            self.tracker.start()
        self._running = True
        self.status_lbl.config(text="Status: Running…")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._start_tick_loop()

    def stop_tracking(self) -> None:
        if not self._running:
            return
        self._stop_tick_loop()
        if self.adv_tracker is not None:
            try:
                self.adv_tracker.stop()
            except Exception:
                pass
            self.adv_tracker = None
        if self.tracker is not None:
            try:
                self.tracker.stop()
            except Exception:
                pass
            self.tracker = None
        # Stop indicator
        try:
            if self.notifier is not None:
                self.notifier.stop_indicator()
        except Exception:
            pass
        self.notifier = None
        self.classifier = None
        # finalize time
        try:
            self.session_data.tick()
        except Exception:
            pass
        self._running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Status: Stopped")
        self._show_summary_dialog()

    def _on_tracker_update(self, status: TrackerStatus, session: Session | None = None) -> None:
        # Called from tracker thread; reflect to Tk and feed SessionData
        def _update() -> None:
            self._basic_status = status
            title = status.window_title or "—"
            self.app_lbl.config(text=f"Active: {title}")
            self._last_title = title
            self._last_focused = bool(status.is_focused)
            self._last_reason = status.reason or ("focused" if status.is_focused else "unfocused")
            state_txt = "Focused" if status.is_focused else f"Unfocused ({status.reason})"
            self.focus_state_lbl.config(text=f"Focus: {state_txt}")
            try:
                self.session_data.add_activity(
                    is_focused=bool(status.is_focused),
                    app=None,
                    title=title,
                )
            except Exception:
                pass
        try:
            self.root.after(0, _update)
        except Exception:
            pass

    def _on_activity_event(self, evt: ActivityEvent) -> None:
        def _update() -> None:
            if evt.type == "app_switch":
                title = str(evt.payload.get("title") or "—")
                app = str(evt.payload.get("app") or "")
                self._last_title = title
                self._last_app = app
                self.app_lbl.config(text=f"Active: {title}")
                # Keep current focus state but update app/title for usage accounting
                self.session_data.add_activity(
                    is_focused=self._last_focused,
                    app=app or None,
                    title=title or None,
                    ts=(evt.ts_ms / 1000.0),
                )
            elif evt.type == "focus_change":
                focused = bool(evt.payload.get("focused", False))
                reason = str(evt.payload.get("reason") or "focused")
                title = str(evt.payload.get("title") or self._last_title)
                app = str(evt.payload.get("app") or self._last_app)
                # Classify distraction and override focused if needed
                if self.classifier is not None:
                    try:
                        res = self.classifier.observe(window_title=title, app_name=app, now_ms=evt.ts_ms)
                        self._last_cls = res
                        if res.is_distracted:
                            focused = False
                            reason = res.category
                    except Exception:
                        pass
                self._last_focused = focused
                self._last_title = title
                self._last_app = app
                self._last_reason = reason
                state_txt = "Focused" if focused else f"Unfocused ({reason})"
                self.focus_state_lbl.config(text=f"Focus: {state_txt}")
                # Record in session data
                self.session_data.add_activity(
                    is_focused=focused,
                    app=app or None,
                    title=title or None,
                    ts=(evt.ts_ms / 1000.0),
                )
                # Update tray icon to reflect focus state
                self._update_tray_image(focused)
                # Notifications
                if self.notifier is not None:
                    try:
                        self.notifier.on_focus_event(
                            focused=focused,
                            category=(reason if not focused else None),
                            reason=reason,
                            ts_ms=evt.ts_ms,
                            title=title,
                            app=app,
                        )
                    except Exception:
                        pass

        try:
            self.root.after(0, _update)
        except Exception:
            pass

    def _tick_loop(self) -> None:
        now = time.time()
        try:
            self.session_data.tick(now)
        except Exception:
            pass

        def _update_counts() -> None:
            fs = _format_hms(self.session_data.focused_s)
            us = _format_hms(self.session_data.unfocused_s)
            self.focus_time_lbl.config(text=f"Focused: {fs}")
            self.unfocus_time_lbl.config(text=f"Unfocused: {us}")

        try:
            self.root.after(0, _update_counts)
        except Exception:
            pass

        # Feed notifier timers even without new events
        if self.notifier is not None:
            try:
                self.notifier.on_focus_event(
                    focused=self._last_focused,
                    category=(self._last_reason if not self._last_focused else None),
                    reason=self._last_reason,
                    ts_ms=int(now * 1000),
                    title=self._last_title,
                    app=self._last_app,
                )
            except Exception:
                pass

        self._tick_job = self.root.after(500, self._tick_loop)

    def _start_tick_loop(self) -> None:
        if self._tick_job is None:
            self._tick_job = self.root.after(500, self._tick_loop)

    def _stop_tick_loop(self) -> None:
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    # -------------- Summary ---------------
    def _show_summary_dialog(self) -> None:
        top = tk.Toplevel(self.root)
        top.title("Session Summary")
        top.geometry("420x260")
        frm = ttk.Frame(top, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        # Numbers
        fs = self.session_data.focused_s
        us = self.session_data.unfocused_s
        total = fs + us
        f_pct = (fs / total * 100.0) if total > 0 else 0.0
        u_pct = (us / total * 100.0) if total > 0 else 0.0

        ttk.Label(frm, text=f"Focused: {_format_hms(fs)} ({f_pct:.1f}%)").pack(anchor="w")
        ttk.Label(frm, text=f"Unfocused: {_format_hms(us)} ({u_pct:.1f}%)").pack(anchor="w", pady=(4, 8))

        # Button row
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(10, 0))

        def _restart() -> None:
            try:
                top.destroy()
            except Exception:
                pass
            self.start_tracking()

        restart_btn = ttk.Button(btns, text="Restart Tracking", command=_restart)
        restart_btn.pack(side=tk.LEFT)

        # Show pie via existing helper
        stats = {"focused_seconds": int(fs), "unfocused_seconds": int(us)}
        show_pie_summary(stats)

    # -------------- Settings & Helpers ---------------
    def _set_keywords(self) -> None:
        current = ", ".join(sorted(self.allowed_keywords))
        resp = simpledialog.askstring(
            "Focus Keywords",
            "Enter keywords to treat as focused (comma-separated):",
            initialvalue=current,
            parent=self.root,
        )
        if resp is not None:
            kws = {k.strip().lower() for k in resp.split(",") if k.strip()}
            self.allowed_keywords = kws
            if self.tracker:
                try:
                    self.tracker.set_allowed_keywords(sorted(kws))
                except Exception:
                    pass
            if self.adv_tracker:
                try:
                    self.adv_tracker.allowed_keywords = sorted(kws)
                except Exception:
                    pass

    def _start_break(self, seconds: int) -> None:
        if self.classifier:
            try:
                self.classifier.set_break(seconds)
            except Exception:
                pass
        if self.notifier:
            try:
                self.notifier.set_break(seconds)
            except Exception:
                pass
        try:
            self.session_data.pause()
        except Exception:
            pass
        self.status_lbl.config(text=f"Status: Break active for {seconds//60} min")

    def _stop_break(self) -> None:
        if self.classifier:
            try:
                self.classifier.set_break_active(False)
            except Exception:
                pass
        if self.notifier:
            try:
                self.notifier.set_break_active(False)
            except Exception:
                pass
        try:
            self.session_data.resume()
        except Exception:
            pass
        self.status_lbl.config(text="Status: Break stopped")

    def _whitelist_current_app(self) -> None:
        if not self.classifier:
            return
        app = self._last_app or None
        if app:
            try:
                self.classifier.add_whitelist_app(app)
                messagebox.showinfo("AURA", f"Whitelisted app {app}")
            except Exception:
                pass

    def _whitelist_current_domain(self) -> None:
        if not self.classifier or not self._last_cls or not self._last_cls.matched_domain:
            return
        dom = self._last_cls.matched_domain
        try:
            self.classifier.add_whitelist_domain(dom)
            messagebox.showinfo("AURA", f"Whitelisted domain {dom}")
        except Exception:
            pass

    # -------------- Tray ------------------
    def minimize_to_tray(self) -> None:
        if _pystray is None:
            messagebox.showinfo("AURA", "System tray support is unavailable. Install 'pystray'.")
            return
        if self._tray_visible:
            return
        # Hide the window
        self.root.withdraw()
        self._tray_visible = True
        self._ensure_tray_running()

    def _ensure_tray_running(self) -> None:
        if not _pystray or (self._tray_icon is not None):
            return

        # Build menu
        def _menu_show(icon, item):  # type: ignore[no-redef]
            self.root.after(0, self._show_from_tray)

        def _menu_toggle(icon, item):  # type: ignore[no-redef]
            self.root.after(0, self._toggle_tracking)

        def _menu_exit(icon, item):  # type: ignore[no-redef]
            self.root.after(0, self._hard_exit)

        menu_cls = getattr(_pystray, "Menu")
        menu_item_cls = getattr(_pystray, "MenuItem")
        icon_cls = getattr(_pystray, "Icon")

        menu = menu_cls(
            menu_item_cls("Show AURA", _menu_show),
            menu_item_cls("Start/Stop Tracking", _menu_toggle),
            menu_item_cls("Exit", _menu_exit),
        )

        image = self._make_tray_image(self._last_focused)
        self._tray_icon = icon_cls("AURA", image, "AURA", menu)

        def _run_icon():
            try:
                assert self._tray_icon is not None
                self._tray_icon.run()
            except Exception:
                pass

        self._tray_thread = threading.Thread(target=_run_icon, daemon=True)
        self._tray_thread.start()

    def _show_from_tray(self) -> None:
        # Stop tray icon and show window
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:
            pass
        self._tray_icon = None
        self._tray_visible = False
        try:
            self.root.deiconify()
            # Bring window to front via after using a helper
            self.root.after(10, self._safe_lift)
        except Exception:
            pass

    def _toggle_tracking(self) -> None:
        if self._running:
            self.stop_tracking()
        else:
            self.start_tracking()

    def _hard_exit(self) -> None:
        try:
            if self._running:
                self.stop_tracking()
        except Exception:
            pass
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:
            pass
        self.root.after(0, self.root.destroy)

    def _safe_lift(self) -> None:
        try:
            self.root.lift()  # type: ignore[call-arg]
        except Exception:
            pass

    def _make_tray_image(self, focused: bool) -> Optional[Any]:
        if Image is None:
            return None
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = (46, 204, 113, 255) if focused else (231, 76, 60, 255)
        draw.ellipse((8, 8, size - 8, size - 8), fill=color)
        return img

    def _update_tray_image(self, focused: bool) -> None:
        if (not _pystray) or (self._tray_icon is None):
            return
        try:
            self._tray_icon.icon = self._make_tray_image(focused)
        except Exception:
            pass

    # -------------- Close -----------------
    def _on_close(self) -> None:
        # If tracking, minimize to tray; else exit
        if self._running and _pystray is not None:
            self.minimize_to_tray()
        else:
            self._hard_exit()


def main() -> int:
    root = tk.Tk()
    AuraMinimalUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
