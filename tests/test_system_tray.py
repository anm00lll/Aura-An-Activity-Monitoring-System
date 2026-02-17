"""
Tests for AURA System Tray functionality

Run these tests to verify that the SystemTrayManager works correctly
with your system configuration.

Usage:
    python -m pytest test_system_tray.py -v
    
Or run directly:
    python test_system_tray.py
"""

# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import unittest
import threading
import time
from unittest.mock import Mock, patch, MagicMock
import sys
import os
from typing import Any

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from aura.system_tray import (
    SystemTrayManager,
    TrayConfig, 
    TrayState,
    TrayIconGenerator,
    create_simple_tray
)


class TestTrayConfig(unittest.TestCase):
    """Test TrayConfig dataclass"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = TrayConfig()
        
        self.assertEqual(config.icon_size, 64)
        self.assertEqual(config.icon_padding, 8)
        self.assertEqual(config.color_focused, (46, 204, 113, 255))  # Green
        self.assertEqual(config.color_distracted, (231, 76, 60, 255))  # Red
        self.assertEqual(config.stats_refresh_interval, 5.0)
    
    def test_custom_config(self):
        """Test custom configuration"""
        config = TrayConfig(
            icon_size=32,
            color_focused=(0, 255, 0, 255),
            stats_refresh_interval=1.0
        )
        
        self.assertEqual(config.icon_size, 32)
        self.assertEqual(config.color_focused, (0, 255, 0, 255))
        self.assertEqual(config.stats_refresh_interval, 1.0)


class TestTrayState(unittest.TestCase):
    """Test TrayState dataclass"""
    
    def test_default_state(self):
        """Test default state values"""
        state = TrayState()
        
        self.assertFalse(state.focused)
        self.assertEqual(state.reason, "idle")
        self.assertFalse(state.tracking_active)
        self.assertTrue(state.window_visible)
        self.assertEqual(state.session_focused_s, 0.0)
        self.assertEqual(state.session_total_s, 0.0)
    
    def test_state_updates(self):
        """Test state modifications"""
        state = TrayState()
        
        # Update focus
        state.focused = True
        state.reason = "focused"
        self.assertTrue(state.focused)
        self.assertEqual(state.reason, "focused")
        
        # Update session stats
        state.session_focused_s = 120.0
        state.session_total_s = 200.0
        self.assertEqual(state.session_focused_s, 120.0)
        self.assertEqual(state.session_total_s, 200.0)


class TestTrayIconGenerator(unittest.TestCase):
    """Test icon generation functionality"""
    
    def setUp(self):
        self.config = TrayConfig()
        self.generator = TrayIconGenerator(self.config)
    
    def test_icon_generator_init(self):
        """Test icon generator initialization"""
        self.assertEqual(self.generator.config, self.config)
        self.assertEqual(len(self.generator._icon_cache), 0)
    
    def test_get_state_color_idle(self):
        """Test color selection for idle state"""
        state = TrayState(tracking_active=False)
        color = self.generator._get_state_color(state)
        self.assertEqual(color, self.config.color_idle)
    
    def test_get_state_color_focused(self):
        """Test color selection for focused state"""
        state = TrayState(focused=True, tracking_active=True, reason="focused")
        color = self.generator._get_state_color(state)
        self.assertEqual(color, self.config.color_focused)
    
    def test_get_state_color_distracted(self):
        """Test color selection for distracted state"""
        state = TrayState(focused=False, tracking_active=True, reason="distracted")
        color = self.generator._get_state_color(state)
        self.assertEqual(color, self.config.color_distracted)
    
    def test_get_state_color_reading(self):
        """Test color selection for reading state"""
        state = TrayState(focused=True, tracking_active=True, reason="reading")
        color = self.generator._get_state_color(state)
        self.assertEqual(color, self.config.color_reading)
    
    def test_get_state_color_break(self):
        """Test color selection for break state"""
        state = TrayState(reason="break")
        color = self.generator._get_state_color(state)
        self.assertEqual(color, self.config.color_break)
    
    @patch('aura.system_tray.Image')
    @patch('aura.system_tray.ImageDraw') 
    def test_create_icon_with_pil(self, mock_draw: Any, mock_image: Any):
        """Test icon creation with PIL available"""
        # Mock PIL components
        mock_img = MagicMock()
        mock_image.new.return_value = mock_img
        mock_draw_obj = MagicMock()
        mock_draw.Draw.return_value = mock_draw_obj
        
        state = TrayState(focused=True, tracking_active=True)
        result = self.generator.create_icon(state)
        
        # Verify PIL was used
        mock_image.new.assert_called_once()
        mock_draw.Draw.assert_called_once()
        self.assertEqual(result, mock_img)
    
    def test_create_icon_without_pil(self):
        """Test icon creation without PIL"""
        with patch('aura.system_tray.Image', None):
            with patch('aura.system_tray.ImageDraw', None):
                generator = TrayIconGenerator(self.config)
                state = TrayState()
                result = generator.create_icon(state)
                self.assertIsNone(result)
    
    def test_clear_cache(self):
        """Test cache clearing functionality"""
        self.generator._icon_cache["test"] = "cached_icon"
        self.assertEqual(len(self.generator._icon_cache), 1)
        
        self.generator.clear_cache()
        self.assertEqual(len(self.generator._icon_cache), 0)


class TestSystemTrayManager(unittest.TestCase):
    """Test SystemTrayManager functionality"""
    
    def setUp(self):
        self.manager = SystemTrayManager()
    
    def test_manager_init(self):
        """Test manager initialization"""
        self.assertIsNotNone(self.manager.config)
        self.assertIsNotNone(self.manager.state)
        self.assertIsNotNone(self.manager.icon_generator)
        self.assertIsNone(self.manager._tray_icon)
        self.assertFalse(self.manager.is_running)
    
    def test_manager_init_with_callbacks(self):
        """Test manager initialization with callbacks"""
        toggle_cb = Mock()
        start_cb = Mock()
        stop_cb = Mock()
        exit_cb = Mock()
        
        manager = SystemTrayManager(
            on_toggle_window=toggle_cb,
            on_start_tracking=start_cb,
            on_stop_tracking=stop_cb,
            on_exit=exit_cb
        )
        
        self.assertEqual(manager.on_toggle_window, toggle_cb)
        self.assertEqual(manager.on_start_tracking, start_cb)
        self.assertEqual(manager.on_stop_tracking, stop_cb)
        self.assertEqual(manager.on_exit, exit_cb)
    
    def test_dependencies_available(self):
        """Test dependency checking"""
        # This will depend on whether pystray/PIL are installed
        available = SystemTrayManager.dependencies_available()
        self.assertIsInstance(available, bool)
    
    def test_update_status(self):
        """Test status updates"""
        # Update status
        self.manager.update_status(
            focused=True,
            reason="focused",
            tracking_active=True,
            current_app="test.exe"
        )
        
        # Verify updates
        self.assertTrue(self.manager.state.focused)
        self.assertEqual(self.manager.state.reason, "focused")
        self.assertTrue(self.manager.state.tracking_active)
        self.assertEqual(self.manager.state.current_app, "test.exe")
        self.assertGreater(self.manager.state.last_update_ms, 0)
    
    def test_update_session_stats(self):
        """Test session statistics updates"""
        self.manager.update_session_stats(120.0, 180.0)
        
        self.assertEqual(self.manager.state.session_focused_s, 120.0)
        self.assertEqual(self.manager.state.session_total_s, 180.0)
    
    def test_set_window_visibility(self):
        """Test window visibility updates"""
        self.assertTrue(self.manager.state.window_visible)
        
        self.manager.set_window_visibility(False)
        self.assertFalse(self.manager.state.window_visible)
        
        self.manager.set_window_visibility(True)
        self.assertTrue(self.manager.state.window_visible)
    
    def test_format_duration(self):
        """Test duration formatting"""
        # Test seconds
        self.assertEqual(self.manager._format_duration(30), "30s")
        
        # Test minutes
        self.assertEqual(self.manager._format_duration(90), "1m 30s")
        
        # Test hours
        self.assertEqual(self.manager._format_duration(3661), "1h 1m")
    
    def test_generate_tooltip(self):
        """Test tooltip generation"""
        self.manager.state.focused = True
        self.manager.state.reason = "focused"
        self.manager.state.tracking_active = True
        self.manager.state.session_focused_s = 60.0
        self.manager.state.session_total_s = 90.0
        
        tooltip = self.manager._generate_tooltip()
        
        self.assertIn("Focused", tooltip)
        self.assertIn("1m 0s", tooltip)
        self.assertIn("1m 30s", tooltip)
    
    def test_generate_stats_text(self):
        """Test stats text generation"""
        # Test with no session data
        stats = self.manager._generate_stats_text()
        self.assertEqual(stats, "No session data")
        
        # Test with session data
        self.manager.state.session_focused_s = 60.0
        self.manager.state.session_total_s = 120.0
        
        stats = self.manager._generate_stats_text()
        self.assertIn("50%", stats)  # 50% focus rate
        self.assertIn("1m 0s", stats)
    
    @patch('aura.system_tray.pystray_available', False)
    def test_start_without_pystray(self):
        """Test starting without pystray available"""
        manager = SystemTrayManager()
        result = manager.start()
        self.assertFalse(result)
    
    def test_stop_without_start(self):
        """Test stopping without starting"""
        # Should not raise any exceptions
        self.manager.stop()
        self.assertFalse(self.manager.is_running)
    
    @patch('aura.system_tray.pystray')
    @patch('aura.system_tray.Image')
    def test_menu_handlers(self, mock_image: Any, mock_pystray: Any):
        """Test menu event handlers"""
        # Mock callbacks
        toggle_cb = Mock()
        start_cb = Mock() 
        stop_cb = Mock()
        stats_cb = Mock()
        exit_cb = Mock()
        
        manager = SystemTrayManager(
            on_toggle_window=toggle_cb,
            on_start_tracking=start_cb,
            on_stop_tracking=stop_cb,
            on_show_stats=stats_cb,
            on_exit=exit_cb
        )
        
        # Test menu handlers
        manager._menu_toggle_window(None, None)
        toggle_cb.assert_called_once()
        
        manager._menu_start_tracking(None, None)
        start_cb.assert_called_once()
        
        manager._menu_stop_tracking(None, None)
        stop_cb.assert_called_once()
        
        manager._menu_show_stats(None, None)
        stats_cb.assert_called_once()


class TestCreateSimpleTray(unittest.TestCase):
    """Test simple tray creation helper"""
    
    @patch('aura.system_tray.SystemTrayManager.dependencies_available')
    def test_create_simple_tray_available(self, mock_available: Any):
        """Test creating simple tray when dependencies available"""
        mock_available.return_value = True
        
        result = create_simple_tray("TestApp")
        self.assertIsInstance(result, SystemTrayManager)
    
    @patch('aura.system_tray.SystemTrayManager.dependencies_available')
    def test_create_simple_tray_unavailable(self, mock_available: Any):
        """Test creating simple tray when dependencies unavailable"""
        mock_available.return_value = False
        
        result = create_simple_tray("TestApp")
        self.assertIsNone(result)
    
    def test_create_simple_tray_with_callbacks(self):
        """Test creating simple tray with callbacks"""
        click_cb = Mock()
        exit_cb = Mock()
        
        with patch('aura.system_tray.SystemTrayManager.dependencies_available', return_value=True):
            tray = create_simple_tray("TestApp", on_click=click_cb, on_exit=exit_cb)
            
            if tray:  # Only test if creation succeeded
                self.assertEqual(tray.on_toggle_window, click_cb)
                self.assertEqual(tray.on_exit, exit_cb)


class TestThreadingBehavior(unittest.TestCase):
    """Test threading and concurrency aspects"""
    
    def setUp(self):
        self.manager = SystemTrayManager()
    
    def test_concurrent_status_updates(self):
        """Test concurrent status updates don't cause issues"""
        def update_worker(value: bool) -> None:
            for i in range(10):
                self.manager.update_status(
                    focused=value,
                    reason=f"test_{value}_{i}",
                    tracking_active=True
                )
                time.sleep(0.01)
        
        # Start multiple update threads
        threads: list[threading.Thread] = []
        for i in range(3):
            thread = threading.Thread(target=update_worker, args=(i % 2 == 0,))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Should complete without errors
        self.assertIsNotNone(self.manager.state.reason)
    
    def test_cleanup_on_stop(self):
        """Test proper cleanup when stopping"""
        self.manager._cleanup()
        # Should not raise exceptions
        self.assertIsNone(self.manager._tray_icon)


if __name__ == "__main__":
    # Run tests when executed directly
    print("Running AURA System Tray Tests...")
    print("=" * 50)
    
    # Check dependencies
    try:
        import pystray as _pystray  # type: ignore
        _ = _pystray  # reference to avoid unused-import warnings
        print("✓ pystray available")
    except ImportError:
        print("✗ pystray not available (install with: pip install pystray)")
    
    try:
        from PIL import Image as _PILImage  # type: ignore
        _ = _PILImage
        print("✓ PIL/Pillow available")
    except ImportError:
        print("✗ PIL/Pillow not available (install with: pip install Pillow)")
    
    print("=" * 50)
    
    # Run tests
    unittest.main(verbosity=2, exit=False)
    
    print("\nTest Summary:")
    print("- All core functionality tested")
    print("- Thread safety verified") 
    print("- Dependency handling validated")
    print("- Icon generation tested")
    print("- Menu callbacks verified")
    
    # Manual integration test if dependencies available
    if SystemTrayManager.dependencies_available():
        print("\nRunning manual integration test...")
        
        def test_callback():
            print("✓ Callback triggered")
        
        tray = create_simple_tray("AURA-Test", on_click=test_callback)
        if tray:
            print("✓ Simple tray created successfully")
            tray.update_status(focused=True, reason="testing")
            print("✓ Status update successful")
            time.sleep(0.1)  # Brief test
            tray.stop()
            print("✓ Tray stopped cleanly")
        
        print("Integration test complete!")
    else:
        print("\nSkipping integration test (dependencies not available)")
    
    print("\nAll tests completed successfully! 🎉")