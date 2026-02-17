"""
System Tray Integration for AURA - AI-based User Routine Analyzer

This module provides a comprehensive system tray implementation with:
- Dynamic focus-based icon colors (green=focused, red=distracted, blue=reading, gray=idle)
- Interactive right-click menu with session controls
- Real-time status updates and session statistics
- Reliable background operation with proper threading
- Graceful system event handling (logout/shutdown)

Usage:
    from aura.system_tray import SystemTrayManager
    
    # Create tray manager with callbacks
    tray_manager = SystemTrayManager(
        on_toggle_window=window_toggle_callback,
        on_start_tracking=start_callback,
        on_stop_tracking=stop_callback,
        on_exit=exit_callback
    )
    
    # Start tray (non-blocking)
    tray_manager.start()
    
    # Update status throughout session
    tray_manager.update_status(focused=True, reason="coding")
    tray_manager.update_session_stats(focused_time=1200, total_time=1500)
    
    # Clean shutdown
    tray_manager.stop()

Icon States:
    - Green (#2ECC71): Focused/productive work
    - Red (#E74C3C): Distracted/unfocused 
    - Blue (#3498DB): Reading/passive focus
    - Gray (#7F8C8D): Idle/inactive
    - Yellow (#F39C12): Break mode active
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any, Dict
import atexit
import signal
import sys

try:
    import pystray  # type: ignore
    from pystray import Menu, MenuItem  # type: ignore
    pystray_available = True
except ImportError:  # pragma: no cover
    pystray = None  # type: ignore[assignment]
    Menu = None  # type: ignore[assignment] 
    MenuItem = None  # type: ignore[assignment]
    pystray_available = False

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]


# ------------------------ Configuration & State ------------------------

@dataclass
class TrayState:
    """Current state of the system tray and application"""
    focused: bool = False
    reason: str = "idle"  # "focused" | "distracted" | "idle" | "reading" | "break"
    tracking_active: bool = False
    window_visible: bool = True
    session_focused_s: float = 0.0
    session_total_s: float = 0.0
    current_app: str = ""
    last_update_ms: int = 0


@dataclass  
class TrayConfig:
    """Configuration for system tray appearance and behavior"""
    # Icon properties
    icon_size: int = 64
    icon_padding: int = 8
    
    # Colors (RGBA tuples)
    color_focused: tuple[int, int, int, int] = (46, 204, 113, 255)    # Green
    color_distracted: tuple[int, int, int, int] = (231, 76, 60, 255)  # Red  
    color_reading: tuple[int, int, int, int] = (52, 152, 219, 255)    # Blue
    color_idle: tuple[int, int, int, int] = (127, 140, 141, 255)      # Gray
    color_break: tuple[int, int, int, int] = (243, 156, 18, 255)      # Yellow
    
    # Tooltip format
    tooltip_template: str = "AURA: {status} | {focused_time} focused of {total_time}"
    
    # Menu refresh rate (seconds)
    stats_refresh_interval: float = 5.0


# ------------------------ Icon Generation ------------------------

class TrayIconGenerator:
    """Generates system tray icons with different states and colors"""
    
    def __init__(self, config: TrayConfig) -> None:
        self.config = config
        self._icon_cache: Dict[str, Any] = {}
    
    def create_icon(self, state: TrayState) -> Optional[Any]:
        """Create a PIL Image icon based on current application state
        
        Args:
            state: Current application state (focus, reason, etc.)
            
        Returns:
            PIL Image object for the tray icon, or None if PIL unavailable
            
        Icon Design:
            - Solid circle with color based on focus state
            - Small indicator dot in corner if tracking is active
            - Semi-transparent when window is hidden
        """
        if Image is None or ImageDraw is None:
            return None
            
        # Determine base color from state
        color = self._get_state_color(state)
        cache_key = f"{color}_{state.tracking_active}_{state.window_visible}"
        
        # Return cached icon if available
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]
        
        # Create new icon
        size = self.config.icon_size
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Main circle (focus state indicator)
        padding = self.config.icon_padding
        main_bounds = (padding, padding, size - padding, size - padding)
        
        # Adjust opacity if window is hidden
        if not state.window_visible:
            color = (*color[:3], int(color[3] * 0.7))  # 70% opacity
            
        draw.ellipse(main_bounds, fill=color)
        
        # Add tracking indicator dot
        if state.tracking_active:
            dot_size = size // 6
            dot_x = size - dot_size - 2
            dot_y = 2
            dot_bounds = (dot_x, dot_y, dot_x + dot_size, dot_y + dot_size)
            draw.ellipse(dot_bounds, fill=(255, 255, 255, 255))  # White dot
        
        # Cache and return
        self._icon_cache[cache_key] = img
        return img
    
    def _get_state_color(self, state: TrayState) -> tuple[int, int, int, int]:
        """Determine icon color based on application state"""
        if state.reason == "break":
            return self.config.color_break
        elif not state.tracking_active:
            return self.config.color_idle
        elif state.focused:
            if state.reason == "reading":
                return self.config.color_reading
            else:
                return self.config.color_focused
        else:
            return self.config.color_distracted
    
    def clear_cache(self) -> None:
        """Clear icon cache to free memory"""
        self._icon_cache.clear()


# ------------------------ System Tray Manager ------------------------

class SystemTrayManager:
    """
    Manages system tray icon, menu, and user interactions for AURA.
    
    Provides real-time status updates, session statistics, and application controls
    through a persistent system tray presence. Handles threading, system events,
    and resource cleanup automatically.
    """
    
    def __init__(
        self,
        on_toggle_window: Optional[Callable[[], None]] = None,
        on_start_tracking: Optional[Callable[[], None]] = None,
        on_stop_tracking: Optional[Callable[[], None]] = None,
        on_show_stats: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        config: Optional[TrayConfig] = None,
    ) -> None:
        """
        Initialize system tray manager with callbacks and configuration.
        
        Args:
            on_toggle_window: Callback to show/hide main application window
            on_start_tracking: Callback to start focus tracking
            on_stop_tracking: Callback to stop focus tracking  
            on_show_stats: Callback to display detailed session statistics
            on_exit: Callback for graceful application shutdown
            config: Tray appearance and behavior configuration
        """
        self.config = config or TrayConfig()
        self.state = TrayState()
        self.icon_generator = TrayIconGenerator(self.config)
        
        # Callbacks
        self.on_toggle_window = on_toggle_window
        self.on_start_tracking = on_start_tracking
        self.on_stop_tracking = on_stop_tracking
        self.on_show_stats = on_show_stats
        self.on_exit = on_exit
        
        # Threading
        self._tray_icon: Optional[Any] = None
        self._tray_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._update_lock = threading.Lock()
        
        # Menu refresh tracking
        self._last_menu_update = 0.0
        
        # Register cleanup handlers
        atexit.register(self._cleanup)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    # -------------------- Public API --------------------
    
    def start(self) -> bool:
        """
        Start the system tray icon and menu.
        
        Returns:
            True if tray started successfully, False if dependencies missing
        """
        if not self.dependencies_available():
            return False
            
        if self._tray_thread and self._tray_thread.is_alive():
            return True  # Already running
        
        self._stop_event.clear()
        self._tray_thread = threading.Thread(
            target=self._run_tray, 
            name="AURA-SystemTray",
            daemon=True
        )
        self._tray_thread.start()
        
        # Brief wait to ensure tray initializes
        time.sleep(0.1)
        return True
    
    def stop(self) -> None:
        """
        Stop the system tray and clean up resources.
        Blocks until tray thread has fully stopped.
        """
        self._stop_event.set()
        
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        
        if self._tray_thread and self._tray_thread.is_alive():
            self._tray_thread.join(timeout=3.0)
        
        self._cleanup()
    
    def update_status(
        self,
        focused: Optional[bool] = None,
        reason: Optional[str] = None,
        tracking_active: Optional[bool] = None,
        current_app: Optional[str] = None,
    ) -> None:
        """
        Update tray icon and tooltip based on current application status.
        
        Args:
            focused: Whether user is currently focused (True/False) 
            reason: Reason for current state ("focused", "distracted", "idle", "reading", "break")
            tracking_active: Whether focus tracking is currently running
            current_app: Name of currently active application
        """
        with self._update_lock:
            changed = False
            
            if focused is not None and self.state.focused != focused:
                self.state.focused = focused
                changed = True
                
            if reason is not None and self.state.reason != reason:
                self.state.reason = reason
                changed = True
                
            if tracking_active is not None and self.state.tracking_active != tracking_active:
                self.state.tracking_active = tracking_active
                changed = True
                
            if current_app is not None:
                self.state.current_app = current_app
                
            self.state.last_update_ms = int(time.time() * 1000)
            
            if changed:
                self._update_icon_async()
    
    def update_session_stats(
        self,
        focused_time: float,
        total_time: float,
    ) -> None:
        """
        Update session statistics displayed in tooltip and menu.
        
        Args:
            focused_time: Total focused time in seconds
            total_time: Total session time in seconds
        """
        with self._update_lock:
            self.state.session_focused_s = focused_time
            self.state.session_total_s = total_time
            self._update_tooltip_async()
    
    def set_window_visibility(self, visible: bool) -> None:
        """
        Update tray icon to reflect main window visibility.
        
        Args:
            visible: Whether main application window is currently visible
        """
        with self._update_lock:
            if self.state.window_visible != visible:
                self.state.window_visible = visible
                self._update_icon_async()
    
    # -------------------- Internal Implementation --------------------
    
    def _run_tray(self) -> None:
        """Main tray thread - creates and runs the pystray icon"""
        try:
            # Create initial icon and menu
            initial_icon = self.icon_generator.create_icon(self.state)
            initial_tooltip = self._generate_tooltip()
            
            menu = self._create_menu()
            
            if pystray is not None:
                self._tray_icon = pystray.Icon(
                    name="AURA",
                    icon=initial_icon,
                    title=initial_tooltip,
                    menu=menu
                )
                
                # Set up click handlers
                self._tray_icon.on_click = self._on_left_click
                
                # Run tray (blocks until stopped)
                self._tray_icon.run()
            
        except Exception as e:
            print(f"AURA Tray Error: {e}")
        finally:
            self._tray_icon = None
    
    def _create_menu(self) -> Any:
        """Create right-click context menu with dynamic content"""
        if Menu is None or MenuItem is None:
            return None
        
        # Dynamic tracking status
        tracking_label = "Stop Tracking" if self.state.tracking_active else "Start Tracking"
        tracking_action = self._menu_stop_tracking if self.state.tracking_active else self._menu_start_tracking
        
        # Dynamic window status  
        window_label = "Hide Window" if self.state.window_visible else "Show Window"
        
        return Menu(
            MenuItem(
                text=window_label,
                action=self._menu_toggle_window,
                default=True  # Bold text, triggered by left-click
            ),
            MenuItem(
                text=tracking_label,
                action=tracking_action,
            ),
            Menu.SEPARATOR,
            MenuItem(
                text="Session Stats",
                action=self._menu_show_stats,
            ),
            MenuItem(
                text=self._generate_stats_text(),
                action=None,  # Display-only item
                enabled=False
            ),
            Menu.SEPARATOR,
            MenuItem(
                text="Exit AURA", 
                action=self._menu_exit
            ),
        )
    
    def _generate_tooltip(self) -> str:
        """Generate tooltip text showing current status and session time"""
        # Format status
        if self.state.reason == "break":
            status = "On Break"
        elif not self.state.tracking_active:
            status = "Tracking Stopped"
        elif self.state.focused:
            status = f"Focused ({self.state.reason.title()})"
        else:
            status = f"Distracted ({self.state.reason.title()})"
        
        # Format times
        focused_time = self._format_duration(self.state.session_focused_s)
        total_time = self._format_duration(self.state.session_total_s)
        
        return self.config.tooltip_template.format(
            status=status,
            focused_time=focused_time,
            total_time=total_time
        )
    
    def _generate_stats_text(self) -> str:
        """Generate quick stats text for menu display"""
        if self.state.session_total_s == 0:
            return "No session data"
        
        focus_pct = (self.state.session_focused_s / self.state.session_total_s) * 100
        focused_time = self._format_duration(self.state.session_focused_s)
        
        return f"Focus: {focus_pct:.0f}% ({focused_time})"
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human-readable string"""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    # -------------------- Event Handlers --------------------
    
    def _on_left_click(self, icon: Any, item: Any) -> None:
        """Handle left-click on tray icon - toggle window visibility"""
        if self.on_toggle_window:
            try:
                self.on_toggle_window()
            except Exception as e:
                print(f"AURA Tray: Window toggle error: {e}")
    
    def _menu_toggle_window(self, icon: Any, item: Any) -> None:
        """Menu handler: Show/hide main window"""
        self._on_left_click(icon, item)
    
    def _menu_start_tracking(self, icon: Any, item: Any) -> None:
        """Menu handler: Start focus tracking"""
        if self.on_start_tracking:
            try:
                self.on_start_tracking()
                # Update menu to reflect new state
                self._update_menu_async()
            except Exception as e:
                print(f"AURA Tray: Start tracking error: {e}")
    
    def _menu_stop_tracking(self, icon: Any, item: Any) -> None:
        """Menu handler: Stop focus tracking"""
        if self.on_stop_tracking:
            try:
                self.on_stop_tracking()
                # Update menu to reflect new state
                self._update_menu_async()
            except Exception as e:
                print(f"AURA Tray: Stop tracking error: {e}")
    
    def _menu_show_stats(self, icon: Any, item: Any) -> None:
        """Menu handler: Show detailed session statistics"""
        if self.on_show_stats:
            try:
                self.on_show_stats()
            except Exception as e:
                print(f"AURA Tray: Show stats error: {e}")
    
    def _menu_exit(self, icon: Any, item: Any) -> None:
        """Menu handler: Exit application"""
        if self.on_exit:
            try:
                self.on_exit()
            except Exception as e:
                print(f"AURA Tray: Exit error: {e}")
        self.stop()
    
    # -------------------- Async Updates --------------------
    
    def _update_icon_async(self) -> None:
        """Update tray icon image asynchronously"""
        if self._tray_icon is None:
            return
        
        try:
            new_icon = self.icon_generator.create_icon(self.state)
            if new_icon:
                self._tray_icon.icon = new_icon
        except Exception as e:
            print(f"AURA Tray: Icon update error: {e}")
    
    def _update_tooltip_async(self) -> None:
        """Update tooltip text asynchronously"""
        if self._tray_icon is None:
            return
        
        try:
            new_tooltip = self._generate_tooltip()
            self._tray_icon.title = new_tooltip
        except Exception as e:
            print(f"AURA Tray: Tooltip update error: {e}")
    
    def _update_menu_async(self) -> None:
        """Update context menu asynchronously (rate-limited)"""
        if self._tray_icon is None:
            return
        
        # Rate limit menu updates to avoid performance issues
        now = time.time()
        if now - self._last_menu_update < self.config.stats_refresh_interval:
            return
        
        try:
            new_menu = self._create_menu()
            if new_menu:
                self._tray_icon.menu = new_menu
                self._last_menu_update = now
        except Exception as e:
            print(f"AURA Tray: Menu update error: {e}")
    
    # -------------------- Cleanup & Shutdown --------------------
    
    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle system signals for graceful shutdown"""
        print(f"AURA Tray: Received signal {signum}, shutting down...")
        self.stop()
        if self.on_exit:
            self.on_exit()
    
    def _cleanup(self) -> None:
        """Clean up resources and clear caches"""
        self.icon_generator.clear_cache()
        self._tray_icon = None
    
    # -------------------- Utility Properties --------------------
    
    @property
    def is_running(self) -> bool:
        """Check if tray is currently active"""
        return (
            self._tray_thread is not None 
            and self._tray_thread.is_alive() 
            and not self._stop_event.is_set()
        )
    
    @property 
    def is_available(self) -> bool:
        """Check if system tray functionality is available"""
        return pystray is not None and Image is not None
    
    @staticmethod
    def dependencies_available() -> bool:
        """Check if system tray dependencies are available"""
        return pystray_available and Image is not None


# ------------------------ Helper Functions ------------------------

def create_simple_tray(
    app_name: str = "AURA",
    on_click: Optional[Callable[[], None]] = None,
    on_exit: Optional[Callable[[], None]] = None
) -> Optional[SystemTrayManager]:
    """
    Create a simple system tray with minimal configuration.
    
    Args:
        app_name: Application name for tray icon
        on_click: Callback for tray icon clicks
        on_exit: Callback for exit action
        
    Returns:
        SystemTrayManager instance or None if not available
    """
    if not SystemTrayManager.dependencies_available():
        return None
    
    return SystemTrayManager(
        on_toggle_window=on_click,
        on_exit=on_exit
    )


# ------------------------ Example Usage ------------------------

if __name__ == "__main__":
    """
    Example usage of SystemTrayManager with mock callbacks.
    Run this script directly to test tray functionality.
    """
    
    def mock_toggle_window():
        print("Mock: Toggle window visibility")
    
    def mock_start_tracking():
        print("Mock: Start tracking")
    
    def mock_stop_tracking():
        print("Mock: Stop tracking")
    
    def mock_show_stats():
        print("Mock: Show detailed statistics")
    
    def mock_exit():
        print("Mock: Exit application")
        import sys
        sys.exit(0)
    
    # Create tray manager
    tray = SystemTrayManager(
        on_toggle_window=mock_toggle_window,
        on_start_tracking=mock_start_tracking,
        on_stop_tracking=mock_stop_tracking,
        on_show_stats=mock_show_stats,
        on_exit=mock_exit
    )
    
    if not tray.start():
        print("System tray not available (missing pystray or PIL)")
        sys.exit(1)
    
    print("System tray started. Right-click icon for menu, left-click to test toggle.")
    print("Press Ctrl+C to exit.")
    
    try:
        # Simulate status updates
        import time
        for i in range(100):
            # Simulate focus changes
            focused = (i % 4) < 2
            reason = "focused" if focused else ("idle" if i % 4 == 2 else "distracted")
            
            tray.update_status(
                focused=focused,
                reason=reason, 
                tracking_active=True,
                current_app="test_app.exe"
            )
            
            # Simulate session progress
            tray.update_session_stats(
                focused_time=i * 10,
                total_time=i * 15
            )
            
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("Shutting down...")
        tray.stop()


__all__ = [
    "SystemTrayManager", 
    "TrayConfig", 
    "TrayState",
    "TrayIconGenerator",
    "create_simple_tray"
]