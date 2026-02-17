from __future__ import annotations

import sys
import time
import os
import json
import tkinter as tk
from tkinter import simpledialog
from typing import Optional, Callable

from .plotter import show_pie_summary
from .session import Session
from .session_data import SessionData
from .tracker import FocusTracker, TrackerStatus
from .activity_tracker import ActivityTracker, ActivityEvent, FocusState
from .classifier import DistractionClassifier, ClassificationResult
from .notification import NotificationManager, NotificationSettings
from .system_tray import SystemTrayManager, TrayConfig
from .distraction_list_editor import DistractionListStore, DistractionListEditor
from .focus_timer import FocusTimer, FocusTimerConfig, FocusTimerWidget


class AuraApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        distraction_store: Optional[DistractionListStore] = None,
        tray_factory: Optional[Callable[[TrayConfig, Callable[[], None], Callable[[], None], Callable[[], None], Callable[[], None]], SystemTrayManager]] = None,
        notifier_factory: Optional[Callable[[NotificationSettings], NotificationManager]] = None,
    ) -> None:
        self.root = root
        self.root.title("AURA - AI-based User Routine Analyzer")
        self.root.geometry("600x260")

        self.session = Session()
        self.session_data = SessionData()
        self.tracker: Optional[FocusTracker] = None
        self.adv_tracker: Optional[ActivityTracker] = None
        self._adv_focus: Optional[FocusState] = None
        self._basic_status: Optional[TrackerStatus] = None
        self._tick_job: Optional[str] = None
        self._ui_running = False
        self.use_advanced = tk.BooleanVar(value=True)
        # Classifier
        self.classifier: Optional[DistractionClassifier] = None
        self._last_cls: Optional[ClassificationResult] = None
        # Notifications
        self.notifier: Optional[NotificationManager] = None
        # Last seen title/app for tray/whitelist context
        self._last_title: str = ""
        self._last_app: str = ""
        # System tray manager
        self.tray_manager: Optional[SystemTrayManager] = None
        self._window_visible = True
        self._last_tray_update: float = 0.0
        # Break state (for tray/status override)
        self._break_active: bool = False
        # Distraction list editor/store
        self.distraction_store = distraction_store or DistractionListStore()
        self._editor_win = None
        # Focus timer
        self.focus_timer = FocusTimer(
            FocusTimerConfig(),
            on_break_start=lambda secs: self._start_break(secs),
            on_break_end=lambda: self._stop_break(),
            notify=lambda title, msg: self._notify(title, msg),
        )

        # DI factories (optional)
        self._tray_factory = tray_factory
        self._notifier_factory = notifier_factory

        # Preferences path
        self._prefs_path = os.path.join(os.path.expanduser("~"), ".aura_prefs.json")
        self._load_prefs()

        self._build_ui()
        self._bind_close()
        self._setup_tray()

    def _build_ui(self) -> None:
        # Menu
        menubar = tk.Menu(self.root)
        session_menu = tk.Menu(menubar, tearoff=0)
        session_menu.add_command(label="Start Session", command=self.start_session)
        session_menu.add_command(label="Stop Session", command=self.stop_session)
        session_menu.add_separator()
        session_menu.add_command(label="Reset Data", command=self.reset_session)
        session_menu.add_separator()
        session_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="Session", menu=session_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Set Focus Keywords", command=self.set_keywords)
        settings_menu.add_checkbutton(
            label="Use Advanced Tracker",
            variable=self.use_advanced,
            onvalue=True,
            offvalue=False,
            command=self._on_toggle_advanced,
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="Start Break (5 min)", command=self._menu_start_break_5)
        settings_menu.add_command(label="Stop Break", command=self._menu_stop_break)
        settings_menu.add_separator()
        settings_menu.add_command(label="Edit Distraction List…", command=self._open_distraction_editor)
        settings_menu.add_separator()
        settings_menu.add_command(label="Whitelist Current App", command=self._whitelist_current_app)
        settings_menu.add_command(label="Whitelist Current Domain", command=self._whitelist_current_domain)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        # Tray menu
        window_menu = tk.Menu(menubar, tearoff=0)
        window_menu.add_command(label="Minimize to Tray", command=self._hide_to_tray)
        menubar.add_cascade(label="Window", menu=window_menu)
        self.root.config(menu=menubar)

        # Body
        pad = 8
        frame = tk.Frame(self.root, padx=pad, pady=pad)
        frame.pack(fill=tk.BOTH, expand=True)

        self.status_lbl = tk.Label(frame, text="Status: Idle", font=("Segoe UI", 12))
        self.status_lbl.pack(anchor="w")

        self.title_lbl = tk.Label(frame, text="Active Window: —", wraplength=580, justify="left")
        self.title_lbl.pack(anchor="w", pady=(4, 8))

        self.focus_lbl = tk.Label(frame, text="Focused: 0s", font=("Consolas", 11))
        self.focus_lbl.pack(anchor="w")

        self.unfocus_lbl = tk.Label(frame, text="Unfocused: 0s", font=("Consolas", 11))
        self.unfocus_lbl.pack(anchor="w")

        # Controls
        btn_frame = tk.Frame(frame)
        btn_frame.pack(anchor="w", pady=(12, 0))
        self.start_btn = tk.Button(btn_frame, text="Start", command=self.start_session, width=10)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = tk.Button(btn_frame, text="Stop", command=self.stop_session, state=tk.DISABLED, width=10)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.summary_btn = tk.Button(btn_frame, text="Show Summary", command=self.show_summary, width=14)
        self.summary_btn.pack(side=tk.LEFT)

        # Focus timer block
        timer_frame = tk.LabelFrame(frame, text="Focus Timer", padx=6, pady=6)
        timer_frame.pack(fill=tk.X, pady=(12, 0))
        self.timer_widget = FocusTimerWidget(timer_frame, self.focus_timer, show_stats=True)
        self.timer_widget.pack(fill=tk.X)

        hint = (
            "Hint: Set focus keywords (comma-separated). If empty, all windows count as focused.\n"
            "Keywords match the active window title."
        )
        self.hint_lbl = tk.Label(frame, text=hint, fg="#666")
        self.hint_lbl.pack(anchor="w", pady=(10, 0))

    def _bind_close(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Track window visibility for tray sync
        self.root.bind("<Map>", self._on_window_show)
        self.root.bind("<Unmap>", self._on_window_hide)

    def _setup_tray(self) -> None:
        """Initialize system tray if dependencies are available."""
        try:
            if not SystemTrayManager.dependencies_available():
                return
            config = TrayConfig()
            if self._tray_factory is not None:
                self.tray_manager = self._tray_factory(
                    config,
                    self._toggle_window_visibility,
                    self.start_session,
                    self.stop_session,
                    self._hard_exit,
                )
            else:
                self.tray_manager = SystemTrayManager(
                    config=config,
                    on_toggle_window=self._toggle_window_visibility,
                    on_start_tracking=self.start_session,
                    on_stop_tracking=self.stop_session,
                    on_exit=self._hard_exit,
                )
            self.tray_manager.start()
            # Initial tray state
            self.tray_manager.update_status(focused=None, tracking_active=False, reason="idle")
        except Exception:
            self.tray_manager = None

    def _on_toggle_advanced(self) -> None:
        # Applies on next session start; show a small hint
        mode = "Advanced" if self.use_advanced.get() else "Basic"
        self.status_lbl.config(text=f"Status: {mode} mode selected (applies on next Start)")

    # ------- Classifier helpers -------
    def _start_break(self, seconds: int) -> None:
        def _do() -> None:
            self._break_active = True
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
            try:
                self.status_lbl.config(text=f"Status: Break active for {seconds//60} min")
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            _do()

    def _stop_break(self) -> None:
        def _do() -> None:
            self._break_active = False
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
            try:
                self.status_lbl.config(text="Status: Break stopped")
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            _do()

    # ----- Menu helpers to drive timer + tracking breaks -----
    def _menu_start_break_5(self) -> None:
        try:
            # Start timer-driven break for 5 minutes (visual countdown)
            self.focus_timer.start_break(5)
        except Exception:
            pass
        # Ensure tracking sensitivity adjusts immediately
        self._start_break(5 * 60)

    def _menu_stop_break(self) -> None:
        try:
            self.focus_timer.reset()
        except Exception:
            pass
        self._stop_break()

    def _whitelist_current_app(self) -> None:
        if not self.classifier:
            return
        # Use last known app from advanced focus or tracker
        app = None
        if self._adv_focus and self._adv_focus.app_name:
            app = self._adv_focus.app_name
        elif self.tracker and self.tracker.status.window_title:
            # basic: cannot get app name here; skip
            app = None
        if app:
            self.classifier.add_whitelist_app(app)
            self.status_lbl.config(text=f"Status: Whitelisted app {app}")

    def _whitelist_current_domain(self) -> None:
        if not self.classifier:
            return
        # Attempt to extract from last classification
        if self._last_cls and self._last_cls.matched_domain:
            dom = self._last_cls.matched_domain
            self.classifier.add_whitelist_domain(dom)
            self.status_lbl.config(text=f"Status: Whitelisted domain {dom}")

    def set_keywords(self) -> None:
        current = ", ".join(sorted(self.session.allowed_apps))
        resp = simpledialog.askstring(
            "Focus Keywords",
            "Enter keywords to treat as focused (comma-separated):",
            initialvalue=current,
            parent=self.root,
        )
        if resp is not None:
            kws = [k.strip() for k in resp.split(",") if k.strip()]
            self.session.allowed_apps = set(kws)
            if self.tracker:
                self.tracker.set_allowed_keywords(kws)
            if self.adv_tracker:
                # Update advanced tracker keywords directly
                self.adv_tracker.allowed_keywords = [k.strip().lower() for k in kws if k.strip()]

    def _on_tracker_update(self, status: TrackerStatus, session: Session) -> None:
        # This is called from the tracker thread; sync to Tk via after
        def _update():
            # record last basic status for tick loop
            self._basic_status = status
            self._last_title = status.window_title or ''
            self._last_app = ''
            self.title_lbl.config(text=f"Active Window: {self._last_title or '—'}")
            state_txt = {
                True: "Focused",
                False: "Unfocused (" + status.reason + ")",
            }[status.is_focused]
            self.status_lbl.config(text=f"Status: {state_txt}")
            # Feed SessionData with this event (app unknown in basic mode)
            try:
                self.session_data.add_activity(
                    is_focused=status.is_focused,
                    app=None,
                    title=status.window_title or None,
                )
            except Exception:
                pass
            # Defer tray update to tick loop

        try:
            self.root.after(0, _update)
        except Exception:
            pass

    # -------- Advanced tracker wiring --------
    def _on_activity_event(self, evt: ActivityEvent) -> None:
        # Convert ActivityTracker events into UI updates; counting happens in a 1s tick loop
        def _update() -> None:
            if evt.type == "app_switch":
                title = str(evt.payload.get("title") or "—")
                self._last_title = title
                self._last_app = str(evt.payload.get("app") or "")
                self.title_lbl.config(text=f"Active Window: {title}")
                # update app/title in session data keeping current focus
                app = self._last_app
                current_focused = self._adv_focus.focused if self._adv_focus is not None else False
                try:
                    self.session_data.add_activity(
                        is_focused=current_focused,
                        app=app or None,
                        title=title or None,
                        ts=(evt.ts_ms / 1000.0),
                    )
                except Exception:
                    pass
            if evt.type == "focus_change":
                focused = bool(evt.payload.get("focused", False))
                reason = str(evt.payload.get("reason") or "focused")
                title = str(evt.payload.get("title") or self._last_title)
                app = str(evt.payload.get("app") or self._last_app)
                # Run distraction classifier on top of advanced tracker
                cls_txt = ""
                if self.classifier is not None:
                    res = self.classifier.observe(window_title=title, app_name=app, now_ms=evt.ts_ms)
                    self._last_cls = res
                    # If classifier says distracted, override focused state
                    if res.is_distracted:
                        focused = False
                        reason = res.category
                        cls_txt = f" [{res.category}, conf {res.confidence}]"
                # Notify manager about every focus change
                notifier = self.notifier
                if notifier:
                    try:
                        notifier.on_focus_event(
                            focused=focused,
                            category=(reason if not focused else None),
                            reason=reason,
                            ts_ms=evt.ts_ms,
                            title=title,
                            app=app,
                        )
                    except Exception:
                        pass
                self._adv_focus = FocusState(focused=focused, reason=reason, window_title=title, app_name=app)
                state_txt = "Focused" if focused else f"Unfocused ({reason})"
                self.status_lbl.config(text=f"Status: {state_txt}")
                self.title_lbl.config(text=f"Active Window: {title or '—'}")
                # Optionally display classifier category inline
                if cls_txt:
                    self.status_lbl.config(text=f"Status: {state_txt}{cls_txt}")
                # Feed session data with the focus change
                try:
                    self.session_data.add_activity(
                        is_focused=focused,
                        app=app or None,
                        title=title or None,
                        ts=(evt.ts_ms / 1000.0),
                    )
                except Exception:
                    pass
                # Defer tray update to tick loop

        try:
            self.root.after(0, _update)
        except Exception:
            pass

    def _tick_loop(self) -> None:
        # Called every 1s while advanced tracker is running
        now = time.time()
        # Determine focus state from advanced or basic tracker
        if self.adv_tracker is not None:
            focused = self._adv_focus.focused if self._adv_focus is not None else False
            reason = self._adv_focus.reason if self._adv_focus is not None else "focused"
            title = self._adv_focus.window_title if self._adv_focus is not None else self._last_title
            app = self._adv_focus.app_name if self._adv_focus is not None else self._last_app
        else:
            focused = self._basic_status.is_focused if self._basic_status is not None else False
            reason = self._basic_status.reason if self._basic_status is not None else "focused"
            title = self._last_title
            app = self._last_app
        # Advance in-memory session store
        try:
            self.session_data.tick(now)
        except Exception:
            pass

        # Feed notifications continuously to allow delay/escalation timers to progress
        notifier = self.notifier
        if notifier:
            try:
                notifier.on_focus_event(
                    focused=focused,
                    category=(reason if not focused else None),
                    reason=reason,
                    ts_ms=int(now * 1000),
                    title=title,
                    app=app,
                )
            except Exception:
                pass

        def _update_counts() -> None:
            # Display counts from SessionData
            fs = int(self.session_data.focused_s)
            us = int(self.session_data.unfocused_s)
            self.focus_lbl.config(text=f"Focused: {fs}s")
            self.unfocus_lbl.config(text=f"Unfocused: {us}s")

        try:
            self.root.after(0, _update_counts)
        except Exception:
            pass

        # Update system tray (throttled to ~1s)
        try:
            if (self.tray_manager is not None) and (now - self._last_tray_update >= 1.0):
                self._update_tray_status()
                self._last_tray_update = now
        except Exception:
            pass

        # Schedule next tick (faster cadence for responsiveness)
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

    def start_session(self) -> None:
        if self.tracker is not None or self.adv_tracker is not None:
            return
        self.session.reset()
        self.session_data.reset()
        if self.use_advanced.get():
            # Advanced tracker path
            self.classifier = DistractionClassifier()
            # Apply user rules from distraction store
            try:
                self.distraction_store.apply_to_classifier(self.classifier)
            except Exception:
                pass
            self._last_cls = None
            self.notifier = (
                self._notifier_factory(NotificationSettings())
                if self._notifier_factory is not None
                else NotificationManager(NotificationSettings())
            )
            self.adv_tracker = ActivityTracker(
                allowed_keywords=sorted(self.session.allowed_apps),
                poll_interval_ms=500,
                default_idle_timeout_s=300,
                default_think_timeout_s=90,
            )
            self.adv_tracker.add_listener(self._on_activity_event)
            self.adv_tracker.start()
            # Start floating indicator (optional as per settings)
            try:
                self.notifier.start_indicator(self.root)
            except Exception:
                pass
            self._adv_focus = None
            self._start_tick_loop()
        else:
            # Basic tracker path
            self.tracker = FocusTracker(
                session=self.session,
                allowed_keywords=sorted(self.session.allowed_apps),
                poll_interval=1.0,
                idle_threshold=60,
                unfocused_notify_after=10,
                on_update=self._on_tracker_update,
            )
            self.tracker.start()
            self._start_tick_loop()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_lbl.config(text="Status: Running…")

    def stop_session(self) -> None:
        if self.tracker is None and self.adv_tracker is None:
            return
        if self.adv_tracker is not None:
            self._stop_tick_loop()
            try:
                self.adv_tracker.stop()
            except Exception:
                pass
            self.adv_tracker = None
            self._adv_focus = None
            self.classifier = None
            self._last_cls = None
            # Stop indicator
            try:
                if self.notifier is not None:
                    self.notifier.stop_indicator()
            except Exception:
                pass
            self.notifier = None
        if self.tracker is not None:
            self._stop_tick_loop()
            self.tracker.stop()
            self.tracker = None
        # Finalize accumulation
        try:
            self.session_data.tick()
        except Exception:
            pass
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Status: Stopped")
        # Optionally show summary when stopping
        self.show_summary()
        # Update tray
        try:
            if self.tray_manager:
                self.tray_manager.update_status(focused=False, tracking_active=False, reason="idle")
        except Exception:
            pass

    def show_summary(self) -> None:
        # Build stats dict from SessionData for the pie chart
        stats = {
            "focused_seconds": int(self.session_data.focused_s),
            "unfocused_seconds": int(self.session_data.unfocused_s),
        }
        show_pie_summary(stats)

    def reset_session(self) -> None:
        if self.tracker or self.adv_tracker:
            self.stop_session()
        self.session.reset()
        self.session_data.reset()
        self._on_tracker_update(TrackerStatus(), self.session)
        self.classifier = None
        self._last_cls = None

    def on_close(self) -> None:
        # Prefer hiding to tray if available
        if self.tray_manager is not None:
            try:
                self._hide_to_tray()
                return
            except Exception:
                pass
        self._hard_exit()

    # ---------- Tray support via SystemTrayManager ----------
    def _toggle_window_visibility(self) -> None:
        if self._window_visible:
            self._hide_to_tray()
        else:
            self._show_from_tray()

    def _hide_to_tray(self) -> None:
        try:
            self.root.withdraw()
            self._window_visible = False
            if self.tray_manager:
                self.tray_manager.set_window_visibility(False)
        except Exception:
            pass

    def _show_from_tray(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()  # type: ignore[call-arg]
            self.root.focus_force()
            self._window_visible = True
            if self.tray_manager:
                self.tray_manager.set_window_visibility(True)
        except Exception:
            pass

    def _hard_exit(self) -> None:
        try:
            if self.tracker is not None or self.adv_tracker is not None:
                self.stop_session()
        except Exception:
            pass
        # Persist preferences
        try:
            self._save_prefs()
        except Exception:
            pass
        # Close editor window if open
        try:
            if self._editor_win is not None:
                self._editor_win.destroy()
        except Exception:
            pass
        try:
            if self.tray_manager is not None:
                self.tray_manager.stop()
        except Exception:
            pass
        try:
            self.session.reset()
            self.session_data.reset()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ----- Preferences -----
    def _load_prefs(self) -> None:
        data: dict[str, object] = {}
        try:
            if os.path.exists(self._prefs_path):
                with open(self._prefs_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        data = raw  # type: ignore[assignment]
        except Exception:
            data = {}
        # Restore use_advanced
        try:
            ua = data.get("use_advanced")
            if isinstance(ua, bool):
                self.use_advanced.set(ua)
        except Exception:
            pass
        # Restore keywords
        try:
            kws_obj = data.get("allowed_keywords")
            if isinstance(kws_obj, list):
                cleaned: list[str] = []
                for item in list(kws_obj):  # type: ignore[list-item]
                    if isinstance(item, (str, int, float)):
                        s = str(item).strip()
                        if s:
                            cleaned.append(s)
                self.session.allowed_apps = set(cleaned)
        except Exception:
            pass
        # Restore geometry
        try:
            geom = data.get("window_geometry")
            if isinstance(geom, str) and geom:
                self.root.geometry(geom)
        except Exception:
            pass

    def _save_prefs(self) -> None:
        data: dict[str, object] = {
            "use_advanced": bool(self.use_advanced.get()),
            "allowed_keywords": sorted(self.session.allowed_apps),
            "window_geometry": self.root.winfo_geometry(),
        }
        try:
            with open(self._prefs_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ---------- Notifications helper for timer ----------
    def _notify(self, title: str, message: str) -> None:
        def _do() -> None:
            try:
                from . import notifier as base_notifier  # local import to avoid cycles
                base_notifier.notify(title, message, timeout=6)
                return
            except Exception:
                pass
            nm = self.notifier
            if nm:
                try:
                    nm._send_notification(title, message, timeout=6)  # type: ignore[attr-defined]
                except Exception:
                    pass
        try:
            self.root.after(0, _do)
        except Exception:
            _do()

    def _on_window_show(self, event: tk.Event) -> None:
        if event.widget == self.root:
            self._window_visible = True
            if self.tray_manager:
                try:
                    self.tray_manager.set_window_visibility(True)
                except Exception:
                    pass

    def _on_window_hide(self, event: tk.Event) -> None:
        if event.widget == self.root:
            self._window_visible = False
            if self.tray_manager:
                try:
                    self.tray_manager.set_window_visibility(False)
                except Exception:
                    pass

    def _update_tray_status(self) -> None:
        if not self.tray_manager:
            return
        # Determine current focus state (break overrides)
        focused = False
        reason = "idle"
        current_app = ""
        if self._break_active:
            focused = False
            reason = "break"
            current_app = self._last_app
        elif self.adv_tracker is not None and self._adv_focus is not None:
            focused = bool(self._adv_focus.focused)
            reason = self._adv_focus.reason or ("focused" if focused else "distracted")
            current_app = self._adv_focus.app_name or ""
        elif self._basic_status is not None:
            focused = bool(self._basic_status.is_focused)
            reason = self._basic_status.reason or ("focused" if focused else "distracted")
            current_app = self._basic_status.window_title or ""

        try:
            self.tray_manager.update_status(
                focused=focused,
                tracking_active=self._running_or_tracking(),
                reason=reason,
                current_app=current_app,
            )
        except Exception:
            pass

        # Update session stats (total vs focused seconds)
        try:
            total_seconds = int(self.session_data.focused_s + self.session_data.unfocused_s)
            focused_seconds = int(self.session_data.focused_s)
            self.tray_manager.update_session_stats(total_seconds, focused_seconds)
        except Exception:
            pass

    def _running_or_tracking(self) -> bool:
        return bool(self.tracker is not None or self.adv_tracker is not None)

    # ---------- Distraction list editor ----------
    def _open_distraction_editor(self) -> None:
        # Reuse single window; if already open, focus it
        if self._editor_win is not None and self._editor_win.winfo_exists():
            try:
                self._editor_win.lift()  # type: ignore[attr-defined]
                self._editor_win.focus_force()
            except Exception:
                pass
            return
        win = tk.Toplevel(self.root)
        win.title("Distraction List Editor")
        win.geometry("720x460")
        # Ensure we clear ref on close
        def _on_close() -> None:
            try:
                win.destroy()
            finally:
                self._editor_win = None
        win.protocol("WM_DELETE_WINDOW", _on_close)
        # Instantiate editor; pass current classifier for live updates
        editor = DistractionListEditor(win, self.distraction_store, self.classifier, on_change=self._on_distraction_list_changed)
        editor.pack(fill=tk.BOTH, expand=True)
        self._editor_win = win

    def _on_distraction_list_changed(self) -> None:
        # Apply updated rules to classifier if active
        if self.classifier is not None:
            try:
                self.distraction_store.apply_to_classifier(self.classifier)
            except Exception:
                pass
        try:
            self.status_lbl.config(text="Status: Distraction rules updated")
        except Exception:
            pass


def main() -> int:
    root = tk.Tk()
    _app = AuraApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
