"""
AURA with System Tray Integration

A simplified AURA application demonstrating proper system tray integration
using the SystemTrayManager for background operation and user interaction.

Usage:
    python aura_with_tray.py

Features:
    - System tray icon with focus status indicators
    - Background operation when window is hidden
    - Tray menu controls for tracking
    - Proper window show/hide functionality
"""

import sys
import time
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Optional

# Import the system tray manager
from .system_tray import SystemTrayManager, TrayConfig


class SimpleAuraApp:
    """
    Simplified AURA application with system tray support.
    
    This demonstrates the core integration patterns without
    complex dependencies on other AURA modules.
    """
    
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AURA - Focus Tracker with System Tray")
        self.root.geometry("500x400")
        
        # Application state
        self.tracking_active = False
        self.session_start_time = 0.0
        self.focused_time = 0.0
        self.current_focus_state = False  # True=focused, False=distracted
        self._window_visible = True
        self._last_update = 0.0
        
        # System tray integration
        self.tray_manager: Optional[SystemTrayManager] = None
        
        # Initialize components
        self._setup_ui()
        self._setup_tray()
        self._setup_window_events()
        
        # Start simulation loop
        self._start_simulation()
    
    def _setup_ui(self) -> None:
        """Set up the main application UI"""
        # Main container
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = tk.Label(
            main_frame, 
            text="AURA Focus Tracker", 
            font=("Arial", 16, "bold"),
            fg="#2C3E50"
        )
        title_label.pack(pady=(0, 20))
        
        # Status display
        self.status_frame = tk.LabelFrame(main_frame, text="Current Status", padx=10, pady=10)
        self.status_frame.pack(fill=tk.X, pady=(0, 20))
        
        self.focus_status_label = tk.Label(
            self.status_frame,
            text="Status: Idle",
            font=("Arial", 12),
            fg="#7F8C8D"
        )
        self.focus_status_label.pack(anchor=tk.W)
        
        self.session_label = tk.Label(
            self.status_frame,
            text="Session: Not started",
            font=("Arial", 10),
            fg="#7F8C8D"
        )
        self.session_label.pack(anchor=tk.W)
        
        # Control buttons
        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(0, 20))
        
        self.start_btn = tk.Button(
            button_frame,
            text="Start Tracking",
            command=self._start_tracking,
            bg="#27AE60",
            fg="white",
            font=("Arial", 11, "bold"),
            width=15
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_btn = tk.Button(
            button_frame,
            text="Stop Tracking",
            command=self._stop_tracking,
            bg="#E74C3C",
            fg="white",
            font=("Arial", 11, "bold"),
            width=15,
            state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=10)
        
        # Tray controls
        tray_frame = tk.LabelFrame(main_frame, text="System Tray", padx=10, pady=10)
        tray_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(
            tray_frame,
            text="The AURA icon in your system tray will change color based on focus status:",
            font=("Arial", 9),
            fg="#34495E"
        ).pack(anchor=tk.W)
        
        colors_text = "🟢 Green = Focused  🔴 Red = Distracted  ⚪ Gray = Break/Idle"
        tk.Label(
            tray_frame,
            text=colors_text,
            font=("Arial", 9),
            fg="#7F8C8D"
        ).pack(anchor=tk.W, pady=(5, 0))
        
        self.hide_btn = tk.Button(
            tray_frame,
            text="Hide to Tray",
            command=self._hide_to_tray,
            bg="#9B59B6",
            fg="white",
            font=("Arial", 10),
            width=12
        )
        self.hide_btn.pack(pady=(10, 0))
        
        # Instructions
        info_frame = tk.LabelFrame(main_frame, text="Instructions", padx=10, pady=10)
        info_frame.pack(fill=tk.BOTH, expand=True)
        
        instructions = [
            "• Start tracking to see focus status simulation",
            "• Hide window to tray to test background operation", 
            "• Left-click tray icon to toggle window visibility",
            "• Right-click tray icon for menu options",
            "• Focus status changes automatically for demonstration"
        ]
        
        for instruction in instructions:
            tk.Label(
                info_frame,
                text=instruction,
                font=("Arial", 9),
                fg="#34495E",
                justify=tk.LEFT
            ).pack(anchor=tk.W, pady=1)
    
    def _setup_tray(self) -> None:
        """Initialize system tray manager"""
        if not SystemTrayManager.dependencies_available():
            messagebox.showwarning(
                "System Tray Unavailable",
                "System tray functionality requires 'pystray' and 'Pillow' packages.\n"
                "Install them with: pip install pystray Pillow"
            )
            return
        
        # Create tray configuration
        config = TrayConfig()
        
        # Create tray manager with callbacks
        self.tray_manager = SystemTrayManager(
            config=config,
            on_toggle_window=self._toggle_window,
            on_start_tracking=self._start_tracking,
            on_stop_tracking=self._stop_tracking,
            on_exit=self._exit_application
        )
        
        # Start the system tray
        if self.tray_manager.start():
            print("✓ System tray started successfully")
            # Set initial status
            self.tray_manager.update_status(focused=None)  # idle state
        else:
            print("✗ Failed to start system tray")
            self.tray_manager = None
    
    def _setup_window_events(self) -> None:
        """Set up window event handlers"""
        # Override close button to hide to tray
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        
        # Track window visibility changes
        self.root.bind("<Map>", self._on_window_show)
        self.root.bind("<Unmap>", self._on_window_hide)
    
    def _start_tracking(self) -> None:
        """Start focus tracking simulation"""
        if self.tracking_active:
            return
        
        self.tracking_active = True
        self.session_start_time = time.time()
        self.focused_time = 0.0
        
        # Update UI
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._update_display()
        
        print("▶️ Tracking started")
    
    def _stop_tracking(self) -> None:
        """Stop focus tracking"""
        if not self.tracking_active:
            return
        
        self.tracking_active = False
        
        # Update UI
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        # Update tray
        if self.tray_manager:
            self.tray_manager.update_status(focused=None)  # idle
        
        self._update_display()
        print("⏸️ Tracking stopped")
    
    def _start_simulation(self) -> None:
        """Start the focus simulation loop"""
        def simulation_loop():
            while True:
                if self.tracking_active:
                    # Simulate focus state changes
                    current_time = time.time()
                    
                    # Change focus state every 5 seconds for demo
                    cycle_time = int(current_time) % 10
                    if cycle_time < 5:
                        self.current_focus_state = True  # focused
                        self.focused_time += 0.5
                    else:
                        self.current_focus_state = False  # distracted
                    
                    # Update UI and tray (from main thread)
                    self.root.after(0, self._update_display)
                
                time.sleep(0.5)
        
        # Start simulation in background thread
        simulation_thread = threading.Thread(target=simulation_loop, daemon=True)
        simulation_thread.start()
    
    def _update_display(self) -> None:
        """Update UI display and system tray"""
        if self.tracking_active:
            # Calculate session stats
            elapsed_time = time.time() - self.session_start_time
            focus_percentage = (self.focused_time / elapsed_time * 100) if elapsed_time > 0 else 0
            
            # Update status labels
            status_text = f"Status: {'🟢 Focused' if self.current_focus_state else '🔴 Distracted'}"
            self.focus_status_label.config(
                text=status_text,
                fg="#27AE60" if self.current_focus_state else "#E74C3C"
            )
            
            session_text = f"Session: {elapsed_time:.0f}s total, {focus_percentage:.0f}% focused"
            self.session_label.config(text=session_text)
            
            # Update system tray
            if self.tray_manager:
                self.tray_manager.update_status(focused=self.current_focus_state)
                self.tray_manager.update_session_stats(int(elapsed_time), int(self.focused_time))
        
        else:
            # Not tracking
            self.focus_status_label.config(text="Status: Idle", fg="#7F8C8D")
            self.session_label.config(text="Session: Not started")
    
    def _toggle_window(self) -> None:
        """Toggle window visibility (called from tray)"""
        if self._window_visible:
            self._hide_to_tray()
        else:
            self._show_from_tray()
    
    def _hide_to_tray(self) -> None:
        """Hide window to system tray"""
        self.root.withdraw()
        self._window_visible = False
        print("🖥️ Window hidden to tray")
    
    def _show_from_tray(self) -> None:
        """Show window from system tray"""
        self.root.deiconify()
        self.root.lift()  # type: ignore
        self.root.focus_force()
        self._window_visible = True
        print("🖥️ Window restored from tray")
    
    def _on_window_close(self) -> None:
        """Handle window close button"""
        if self.tray_manager:
            # Hide to tray instead of exiting
            self._hide_to_tray()
        else:
            # No tray available, exit normally
            self._exit_application()
    
    def _on_window_show(self, event: tk.Event) -> None:
        """Handle window show event"""
        if event.widget == self.root:
            self._window_visible = True
    
    def _on_window_hide(self, event: tk.Event) -> None:
        """Handle window hide event"""  
        if event.widget == self.root:
            self._window_visible = False
    
    def _exit_application(self) -> None:
        """Exit the application gracefully"""
        print("👋 Exiting AURA...")
        
        # Stop tracking
        self._stop_tracking()
        
        # Stop system tray
        if self.tray_manager:
            self.tray_manager.stop()
        
        # Exit
        self.root.quit()


def main() -> int:
    """Main entry point"""
    print("="*50)
    print("🌟 AURA Focus Tracker with System Tray")
    print("="*50)
    
    try:
        # Create and run application
        root = tk.Tk()
        app = SimpleAuraApp(root)
        
        # Show welcome message if tray is available
        if app.tray_manager:
            messagebox.showinfo(
                "AURA System Tray Active",
                "AURA is now running with system tray support!\n\n"
                "• Left-click tray icon to show/hide window\n"
                "• Right-click tray icon for menu\n"
                "• Close button hides to tray\n"
                "• Use tray menu 'Exit' to quit completely"
            )
        
        # Start main event loop
        root.mainloop()
        
        return 0
        
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
        return 1
    except Exception as e:
        print(f"❌ Application error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())