import sys
import ctypes

# CRITICAL: Prevent multiple instances using Windows mutex
if getattr(sys, 'frozen', False):
    # Create a named mutex - if it already exists, another instance is running
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ClaudeUsageBarMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)  # Another instance exists, exit immediately

import subprocess

def install_requirements():
    """Auto-install missing dependencies (skip if frozen exe)"""
    if getattr(sys, 'frozen', False):
        return  # Skip when running as exe

    required = {
        'PyQt5': 'PyQt5',
        'requests': 'requests',
        'pystray': 'pystray',
        'PIL': 'pillow',
        'dateutil': 'python-dateutil',
        'cloudscraper': 'cloudscraper',
        'keyboard': 'keyboard',
        'undetected_chromedriver': 'undetected-chromedriver',
    }

    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            print(f"Installing {package}...")
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', package, '-q'])
            except subprocess.CalledProcessError:
                print(f"Warning: Failed to install {package}")

install_requirements()

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
                             QVBoxLayout, QHBoxLayout, QFrame, QSlider, QSpinBox,
                             QCheckBox, QLineEdit, QScrollArea, QDialog,
                             QColorDialog, QMessageBox)
from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QPixmap, QPainter, QBrush
import json
import os
import requests
from datetime import datetime
from pathlib import Path
import threading
import time
import ctypes
from ctypes import wintypes
import logging

# Setup logging
log_path = Path(os.getenv('APPDATA')) / 'ClaudeUsageBar' / 'debug.log'
log_path.parent.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(log_path),
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.info("App starting")

# System Tray
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# Undetected Chrome for auto session grab
try:
    import undetected_chromedriver as uc
    CHROMEDRIVER_AVAILABLE = True
    logging.info("undetected_chromedriver loaded successfully")
except ImportError as e:
    CHROMEDRIVER_AVAILABLE = False
    logging.warning(f"undetected_chromedriver not available: {e}")

# Global hotkeys
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False



class HotkeyEdit(QLineEdit):
    """Custom QLineEdit that captures hotkey combinations"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Press keys...")

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # Ignore lone modifier keys
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            event.accept()
            return

        # Clear on Escape or Backspace
        if key == Qt.Key_Escape or key == Qt.Key_Backspace:
            self.setText("")
            event.accept()
            return

        parts = []
        if modifiers & Qt.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.AltModifier:
            parts.append("alt")
        if modifiers & Qt.ShiftModifier:
            parts.append("shift")

        # Get key name
        key_name = None
        if Qt.Key_A <= key <= Qt.Key_Z:
            key_name = chr(key).lower()
        elif Qt.Key_0 <= key <= Qt.Key_9:
            key_name = chr(key)
        elif Qt.Key_F1 <= key <= Qt.Key_F12:
            key_name = f"f{key - Qt.Key_F1 + 1}"
        elif key == Qt.Key_Space:
            key_name = "space"
        elif key == Qt.Key_Return or key == Qt.Key_Enter:
            key_name = "enter"
        elif key == Qt.Key_Tab:
            key_name = "tab"
        elif key == Qt.Key_Up:
            key_name = "up"
        elif key == Qt.Key_Down:
            key_name = "down"
        elif key == Qt.Key_Left:
            key_name = "left"
        elif key == Qt.Key_Right:
            key_name = "right"
        elif key == Qt.Key_Home:
            key_name = "home"
        elif key == Qt.Key_End:
            key_name = "end"
        elif key == Qt.Key_Delete:
            key_name = "delete"
        elif key == Qt.Key_Insert:
            key_name = "insert"

        if key_name and parts:  # Require at least one modifier
            parts.append(key_name)
            self.setText("+".join(parts))
            event.accept()
        else:
            event.accept()  # Block all other input

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()


class ClaudeUsageBar(QMainWindow):
    # Signals for hotkey callbacks (thread-safe)
    hotkey_clickthrough_signal = pyqtSignal()
    hotkey_compact_signal = pyqtSignal()
    hotkey_refresh_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.NoFocus)

        # Connect hotkey signals
        self.hotkey_clickthrough_signal.connect(self.toggle_clickthrough)
        self.hotkey_compact_signal.connect(self.toggle_compact_mode)
        self.hotkey_refresh_signal.connect(self.manual_refresh)

        # Paths
        self.app_data_dir = Path(os.getenv('APPDATA')) / 'ClaudeUsageBar'
        self.app_data_dir.mkdir(exist_ok=True)
        self.config_file = self.app_data_dir / 'config.json'

        # Load config
        self.config = self.load_config()

        # Validate position
        self.validate_position()

        # State
        self.dragging = False
        self.drag_position = QPoint()
        self.usage_data = None
        self.polling_active = True
        self.driver = None
        self.login_in_progress = False
        self.settings_window = None
        self.clickthrough_enabled = False

        # New feature states
        self.api_status = 'unknown'
        self.last_api_error = None
        self.retry_count = 0
        self.tray_icon = None
        self.is_hidden = False
        self.floating_btn = None  # Floating clickthrough button for when clickthrough is active
        self.last_five_hour_utilization = 0
        self.last_weekly_utilization = 0
        self.snapped_edge = None
        self.collapsed = False
        self.notified_thresholds = set()  # Track which thresholds have been notified
        self.initial_thresholds_set = False  # Skip notifications on first fetch
        self.usage_history = []  # Track usage for prediction (timestamp, utilization)

        # Setup window
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Setup UI
        self.setup_ui()
        self.position_window()
        self.create_floating_button()

        # Apply compact mode if enabled
        if self.config.get('compact_mode', False):
            self.apply_compact_mode()

        # Force topmost using Windows API
        self.force_topmost()

        # Start periodic monitor check
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.check_monitor_bounds)
        self.monitor_timer.start(2000)  # Check every 2 seconds

        # Initialize system tray
        if TRAY_AVAILABLE:
            self.create_tray_icon()

        # Check if we have auth token
        logging.info(f"Session key present: {bool(self.config.get('session_key'))}")
        if not self.config.get('session_key'):
            QTimer.singleShot(500, self.show_login_dialog)
        else:
            logging.info("Starting polling...")
            self.start_polling()

        # Handle start minimized
        if self.config.get('start_minimized', False) and TRAY_AVAILABLE:
            QTimer.singleShot(100, self._start_minimized)

        # Setup global hotkeys
        if KEYBOARD_AVAILABLE:
            self.setup_hotkeys()

    def load_config(self):
        default = {
            'position': {'x': 20, 'y': 80},
            'opacity': 0.9,
            'session_key': None,
            'poll_interval': 60,
            'minimize_to_tray': False,
            'compact_mode': False,
            'snap_mode': 'off',
            'auto_refresh_session': False,
            'five_hour_color': '#CC785C',
            'weekly_color': '#8B6BB7',
            'border_color': '#FFFFFF',
            'show_border': False,
            'text_background': False,
            'text_background_opacity': 70,
            'dynamic_bar_color': True,
            'notifications_enabled': True,
            'notification_thresholds': [70, 90],
            'auto_start': False,
            'start_minimized': False,
            'warning_color_70': '#ffaa44',
            'warning_color_90': '#ff4444',
            'sound_alerts': False,
            'progress_bar_height': 12,
            'font_size': 15,
            'hotkey_clickthrough': 'ctrl+alt+c',
            'hotkey_compact': 'ctrl+alt+m',
            'hotkey_refresh': 'ctrl+alt+r',
            'show_prediction': True,
            'sound_volume': 100
        }

        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    return {**default, **loaded}
            except:
                pass

        return default

    def save_config(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def get_monitors(self):
        """Enumerate all active monitors using ctypes"""
        monitors = []
        try:
            def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
                rect = lprcMonitor.contents
                monitors.append({
                    'left': rect.left,
                    'top': rect.top,
                    'right': rect.right,
                    'bottom': rect.bottom
                })
                return True

            MonitorEnumProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(wintypes.RECT),
                wintypes.LPARAM
            )

            ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(monitor_enum_proc), 0)
        except Exception:
            pass

        return monitors

    def validate_position(self):
        """Ensure the window position is within a visible monitor"""
        x = self.config['position']['x']
        y = self.config['position']['y']

        monitors = self.get_monitors()

        if not monitors:
            return

        is_visible = False
        for m in monitors:
            if (m['left'] <= x < m['right'] - 10) and (m['top'] <= y < m['bottom'] - 10):
                is_visible = True
                break

        if not is_visible:
            self.config['position']['x'] = 20
            self.config['position']['y'] = 80
            self.save_config()

    def check_monitor_bounds(self):
        """Periodically check if window is still on a visible monitor"""
        if not self.dragging:
            x = self.x()
            y = self.y()

            monitors = self.get_monitors()

            if monitors:
                is_visible = False
                for m in monitors:
                    if (m['left'] <= x < m['right'] - 10) and (m['top'] <= y < m['bottom'] - 10):
                        is_visible = True
                        break

                if not is_visible:
                    self.move(20, 80)
                    self.config['position']['x'] = 20
                    self.config['position']['y'] = 80
                    self.save_config()

    def setup_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(1, 1, 1, 1)
        main_layout.setSpacing(0)
        central.setLayout(main_layout)

        # Main frame
        self.main_frame = QFrame()
        self.main_frame.setStyleSheet("background-color: #1a1a1a;")
        self.main_frame.setCursor(QCursor(Qt.ArrowCursor))
        main_layout.addWidget(self.main_frame)

        self.frame_layout = QVBoxLayout()
        self.frame_layout.setContentsMargins(6, 6, 6, 6)
        self.frame_layout.setSpacing(0)
        self.main_frame.setLayout(self.frame_layout)

        # Header
        self.setup_header(self.frame_layout)

        # Content
        self.setup_content(self.frame_layout)

        # Set window size and apply background opacity
        self.setFixedSize(300, 240)
        self.full_height = 240  # Store for non-compact mode restore
        self.apply_background_opacity()
        self.apply_progress_bar_height()  # Apply saved bar height (also adjusts window)
        self.apply_font_size()
        self.apply_border()
        self.apply_text_backgrounds()

    def apply_border(self):
        """Apply border setting to progress bars"""
        if self.config.get('show_border', False):
            border_color = self.config.get('border_color', '#FFFFFF')
            self.five_hour_border_overlay.setGeometry(0, 0, self.five_hour_progress_bg.width(), self.five_hour_progress_bg.height())
            self.five_hour_border_overlay.setStyleSheet(f"background-color: transparent; border: 1px solid {border_color};")
            self.five_hour_border_overlay.show()
            self.five_hour_border_overlay.raise_()
            self.weekly_border_overlay.setGeometry(0, 0, self.weekly_progress_bg.width(), self.weekly_progress_bg.height())
            self.weekly_border_overlay.setStyleSheet(f"background-color: transparent; border: 1px solid {border_color};")
            self.weekly_border_overlay.show()
            self.weekly_border_overlay.raise_()
        else:
            self.five_hour_border_overlay.hide()
            self.weekly_border_overlay.hide()

    def apply_font_size(self):
        """Apply font size to all labels"""
        size = self.config.get('font_size', 15)
        self.five_hour_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {size}px;")
        self.five_hour_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {size}px;")
        self.weekly_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {size}px;")
        self.weekly_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {size}px;")

        # Adjust window height (4 labels affected, ~1.5px extra per px above default 15)
        self.recalculate_window_height()

        # Reapply text backgrounds if enabled
        if self.config.get('text_background', False):
            self.apply_text_backgrounds()

    def apply_progress_bar_height(self):
        """Apply progress bar height setting"""
        height = self.config.get('progress_bar_height', 12)
        self.five_hour_progress_bg.setFixedHeight(height)
        self.weekly_progress_bg.setFixedHeight(height)

        self.recalculate_window_height()

        # Reapply border if enabled
        if self.config.get('show_border', False):
            QTimer.singleShot(0, self.apply_border)

    def recalculate_window_height(self):
        """Recalculate window height based on font size and bar height"""
        base_height = 240

        # Extra for progress bar height (default 12px)
        bar_height = self.config.get('progress_bar_height', 12)
        bar_extra = (bar_height - 12) * 2  # *2 for both bars

        # Extra for font size (default 15px, 4 labels affected)
        font_size = self.config.get('font_size', 15)
        font_extra = int((font_size - 15) * 4)  # 4 labels

        new_full_height = base_height + bar_extra + font_extra
        self.full_height = new_full_height

        if not self.config.get('compact_mode', False):
            self.setFixedSize(300, new_full_height)

    def apply_text_backgrounds(self):
        """Apply background to text labels"""
        size = self.config.get('font_size', 15)
        if self.config.get('text_background', False):
            opacity = int(self.config.get('text_background_opacity', 70) * 255 / 100)
            bg_style = f"background-color: rgba(42, 42, 42, {opacity}); border-radius: 2px;"
            self.five_hour_title.setStyleSheet(f"{bg_style} color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.five_hour_usage_label.setStyleSheet(f"{bg_style} color: #cccccc; font-size: {size}px;")
            self.five_hour_reset_label.setStyleSheet(f"{bg_style} color: #999999; font-size: {size}px;")
            self.prediction_label.setStyleSheet(f"{bg_style} color: #aaaaaa; font-size: 11px; font-style: italic;")
            self.weekly_title.setStyleSheet(f"{bg_style} color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.weekly_usage_label.setStyleSheet(f"{bg_style} color: #cccccc; font-size: {size}px;")
            self.weekly_reset_label.setStyleSheet(f"{bg_style} color: #999999; font-size: {size}px;")
        else:
            self.five_hour_title.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.five_hour_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {size}px;")
            self.five_hour_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {size}px;")
            self.prediction_label.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 11px; font-style: italic;")
            self.weekly_title.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.weekly_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {size}px;")
            self.weekly_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {size}px;")

    def showEvent(self, event):
        """Update border geometry when window is shown"""
        super().showEvent(event)
        # Reapply border after layout is finalized
        if self.config.get('show_border', False):
            QTimer.singleShot(0, self.apply_border)

    def setup_header(self, parent_layout):
        """Setup header with title and buttons"""
        self.header = QFrame()
        self.header.setStyleSheet("background: transparent;")
        self.header.setFixedHeight(28)
        parent_layout.addWidget(self.header)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(4)
        self.header.setLayout(header_layout)

        # Clickthrough button
        self.clickthrough_btn = QPushButton("👆")
        self.clickthrough_btn.setToolTip("Enable Clickthrough")
        self.clickthrough_btn.setFixedSize(24, 24)
        self.clickthrough_btn.setAutoFillBackground(False)
        self.clickthrough_btn.setFocusPolicy(Qt.NoFocus)
        self.clickthrough_btn.setAttribute(Qt.WA_Hover, True)
        # Retain space when hidden so layout doesn't shift
        sp = self.clickthrough_btn.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.clickthrough_btn.setSizePolicy(sp)
        self.clickthrough_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.01);
                color: #aaaaaa;
                border: none;
                font-size: 14px;
                padding: 5px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: #ffffff;
                border: none;
                border-radius: 3px;
            }
            QPushButton:focus {
                outline: none;
                border: none;
            }
        """)
        self.clickthrough_btn.clicked.connect(self.toggle_clickthrough)
        self.clickthrough_btn.setCursor(QCursor(Qt.PointingHandCursor))
        header_layout.addWidget(self.clickthrough_btn)

        # API status dot
        self.api_status_dot = QLabel("●")
        self.api_status_dot.setAutoFillBackground(False)
        self.api_status_dot.setAttribute(Qt.WA_TranslucentBackground)
        self.api_status_dot.setStyleSheet("color: #aaaaaa; background: transparent; font-size: 12px; padding: 0px; margin: 0px;")
        self.api_status_dot.setFixedSize(12, 12)
        header_layout.addWidget(self.api_status_dot)

        # Title label (draggable)
        self.title_label = QLabel("Claude Usage")
        self.title_label.setStyleSheet("""
            background: transparent;
            color: #CC785C;
            font-weight: bold;
            font-size: 15px;
            padding: 0px;
            margin: 0px;
            line-height: 1;
        """)
        self.title_label.setContentsMargins(0, 0, 0, 0)
        self.title_label.setFixedHeight(18)
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        # Button frame
        self.btn_frame = QFrame()
        self.btn_frame.setAutoFillBackground(False)
        self.btn_frame.setAttribute(Qt.WA_TranslucentBackground)
        self.btn_frame.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        self.btn_frame.setLayout(btn_layout)
        header_layout.addWidget(self.btn_frame)

        # Compact button
        self.compact_btn = QPushButton("─")
        self.compact_btn.setToolTip("Compact")
        self.compact_btn.setFixedSize(24, 24)
        self.compact_btn.setAutoFillBackground(False)
        self.compact_btn.setFocusPolicy(Qt.NoFocus)
        self.compact_btn.setAttribute(Qt.WA_Hover, True)
        self.compact_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.01);
                color: #aaaaaa;
                border: 1px solid transparent;
                font-size: 15px;
                padding: 3px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(204, 120, 92, 0.3);
                color: #CC785C;
                border: 1px solid transparent;
                border-radius: 3px;
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid transparent;
            }
        """)
        self.compact_btn.clicked.connect(self.toggle_compact_mode)
        self.compact_btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn_layout.addWidget(self.compact_btn)

        # Refresh button
        self.refresh_btn = QPushButton("⟲")
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setAutoFillBackground(False)
        self.refresh_btn.setFocusPolicy(Qt.NoFocus)
        self.refresh_btn.setAttribute(Qt.WA_Hover, True)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.01);
                color: #aaaaaa;
                border: 1px solid transparent;
                font-size: 15px;
                font-weight: bold;
                padding: 3px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(204, 120, 92, 0.3);
                color: #CC785C;
                border: 1px solid transparent;
                border-radius: 3px;
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid transparent;
            }
        """)
        self.refresh_btn.clicked.connect(self.manual_refresh)
        self.refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn_layout.addWidget(self.refresh_btn)

        # Settings button
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setAutoFillBackground(False)
        self.settings_btn.setFocusPolicy(Qt.NoFocus)
        self.settings_btn.setAttribute(Qt.WA_Hover, True)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.01);
                color: #aaaaaa;
                border: 1px solid transparent;
                font-size: 12px;
                padding: 5px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: #ffffff;
                border: 1px solid transparent;
                border-radius: 3px;
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid transparent;
            }
        """)
        self.settings_btn.clicked.connect(self.show_settings)
        self.settings_btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn_layout.addWidget(self.settings_btn)

        # Close button
        self.close_btn = QPushButton("×")
        self.close_btn.setToolTip("Close")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setAutoFillBackground(False)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setAttribute(Qt.WA_Hover, True)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.01);
                color: #aaaaaa;
                border: 1px solid transparent;
                font-size: 15px;
                font-weight: bold;
                padding: 3px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(255, 68, 68, 0.3);
                color: #ff4444;
                border: 1px solid transparent;
                border-radius: 3px;
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid transparent;
            }
        """)
        self.close_btn.clicked.connect(self.on_close)
        self.close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn_layout.addWidget(self.close_btn)

    def setup_content(self, parent_layout):
        """Setup content area with progress bars"""
        self.content_frame = QFrame()
        self.content_frame.setStyleSheet("background-color: #1a1a1a;")
        self.content_frame.setCursor(QCursor(Qt.ArrowCursor))
        parent_layout.addWidget(self.content_frame)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(2)
        self.content_frame.setLayout(self.content_layout)

        # 5-Hour section
        self.five_hour_title = QLabel("5-Hour Limit")
        self.five_hour_title.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
        self.content_layout.addWidget(self.five_hour_title, alignment=Qt.AlignLeft)

        self.five_hour_usage_label = QLabel("Loading...")
        self.five_hour_usage_label.setStyleSheet("background: transparent; color: #cccccc; font-size: 15px;")
        self.content_layout.addWidget(self.five_hour_usage_label, alignment=Qt.AlignLeft)

        # 5-Hour progress bar (no fixed width - expands to fill available space)
        self.five_hour_progress_bg = QFrame()
        self.five_hour_progress_bg.setFixedHeight(12)
        self.five_hour_progress_bg.setStyleSheet("background-color: #2a2a2a;")
        self.content_layout.addWidget(self.five_hour_progress_bg)

        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.five_hour_progress_bg.setLayout(progress_layout)

        self.five_hour_progress_fill = QFrame()
        self.five_hour_progress_fill.setStyleSheet(f"background-color: {self.config.get('five_hour_color', '#CC785C')}; border: none;")
        self.five_hour_progress_fill.setFixedWidth(0)
        progress_layout.addWidget(self.five_hour_progress_fill, alignment=Qt.AlignLeft)

        # Border overlay (transparent, only shows border in clickthrough mode)
        self.five_hour_border_overlay = QFrame(self.five_hour_progress_bg)
        self.five_hour_border_overlay.setStyleSheet("background-color: transparent; border: none;")
        self.five_hour_border_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.five_hour_border_overlay.setCursor(QCursor(Qt.ArrowCursor))
        self.five_hour_border_overlay.hide()

        self.five_hour_reset_label = QLabel("Resets in: --")
        self.five_hour_reset_label.setStyleSheet("background: transparent; color: #999999; font-size: 15px;")
        self.content_layout.addWidget(self.five_hour_reset_label, alignment=Qt.AlignLeft)

        # Prediction label
        self.prediction_label = QLabel("→ 100% in ~—")
        self.prediction_label.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 11px; font-style: italic;")
        if not self.config.get('show_prediction', True):
            self.prediction_label.hide()
        self.content_layout.addWidget(self.prediction_label, alignment=Qt.AlignLeft)

        self.spacer1 = QWidget()
        self.spacer1.setFixedHeight(10)
        self.spacer1.setStyleSheet("background: transparent;")
        self.content_layout.addWidget(self.spacer1)

        # Separator
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.HLine)
        self.separator.setStyleSheet("background: transparent; color: #333333;")
        self.separator.setFixedHeight(1)
        self.content_layout.addWidget(self.separator)

        self.spacer2 = QWidget()
        self.spacer2.setFixedHeight(8)
        self.spacer2.setStyleSheet("background: transparent;")
        self.content_layout.addWidget(self.spacer2)

        # Weekly section
        self.weekly_title = QLabel("Weekly Limit")
        self.weekly_title.setStyleSheet("background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
        self.content_layout.addWidget(self.weekly_title, alignment=Qt.AlignLeft)

        self.weekly_usage_label = QLabel("Loading...")
        self.weekly_usage_label.setStyleSheet("background: transparent; color: #cccccc; font-size: 15px;")
        self.content_layout.addWidget(self.weekly_usage_label, alignment=Qt.AlignLeft)

        # Weekly progress bar (no fixed width - expands to fill available space)
        self.weekly_progress_bg = QFrame()
        self.weekly_progress_bg.setFixedHeight(12)
        self.weekly_progress_bg.setStyleSheet("background-color: #2a2a2a;")
        self.content_layout.addWidget(self.weekly_progress_bg)

        weekly_progress_layout = QHBoxLayout()
        weekly_progress_layout.setContentsMargins(0, 0, 0, 0)
        self.weekly_progress_bg.setLayout(weekly_progress_layout)

        self.weekly_progress_fill = QFrame()
        self.weekly_progress_fill.setStyleSheet(f"background-color: {self.config.get('weekly_color', '#8B6BB7')}; border: none;")
        self.weekly_progress_fill.setFixedWidth(0)
        weekly_progress_layout.addWidget(self.weekly_progress_fill, alignment=Qt.AlignLeft)

        # Border overlay (transparent, only shows border in clickthrough mode)
        self.weekly_border_overlay = QFrame(self.weekly_progress_bg)
        self.weekly_border_overlay.setStyleSheet("background-color: transparent; border: none;")
        self.weekly_border_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.weekly_border_overlay.setCursor(QCursor(Qt.ArrowCursor))
        self.weekly_border_overlay.hide()

        self.weekly_reset_label = QLabel("Resets in: --")
        self.weekly_reset_label.setStyleSheet("background: transparent; color: #999999; font-size: 15px;")
        self.content_layout.addWidget(self.weekly_reset_label, alignment=Qt.AlignLeft)

    def apply_background_opacity(self):
        """Apply opacity to background elements only, keeping text/bars fully visible"""
        opacity = self.config.get('opacity', 0.9)
        alpha = int(opacity * 255)

        # Single background color for everything - #1a1a1a = rgb(26, 26, 26)
        bg = f"rgba(26, 26, 26, {alpha})"

        # Only apply background to outermost frame - inner frames are transparent
        # Use minimum 1% opacity so users can still drag the window at 0%
        min_alpha = max(alpha, 3)
        self.main_frame.setStyleSheet(f"background-color: rgba(26, 26, 26, {min_alpha});")
        self.content_frame.setStyleSheet("background: transparent;")
        self.header.setStyleSheet("background: transparent;")

        # Apply to labels (only if not in clickthrough mode)
        if not self.clickthrough_enabled:
            font_size = self.config.get('font_size', 15)
            self.five_hour_title.setStyleSheet(f"background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.five_hour_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {font_size}px;")
            self.five_hour_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {font_size}px;")
            self.weekly_title.setStyleSheet(f"background: transparent; color: #aaaaaa; font-size: 12px; font-weight: bold;")
            self.weekly_usage_label.setStyleSheet(f"background: transparent; color: #cccccc; font-size: {font_size}px;")
            self.weekly_reset_label.setStyleSheet(f"background: transparent; color: #999999; font-size: {font_size}px;")

        # Update floating button to match
        self.update_floating_button_style()

    def position_window(self):
        """Position window at saved coordinates"""
        x = self.config['position']['x']
        y = self.config['position']['y']
        self.move(x, y)

    def force_topmost(self):
        """Force window to stay on top using Windows API"""
        try:
            import ctypes
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

            # Also apply to floating button if visible
            if self.floating_btn and self.floating_btn.isVisible():
                hwnd_float = int(self.floating_btn.winId())
                ctypes.windll.user32.SetWindowPos(hwnd_float, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            pass

    def create_floating_button(self):
        """Create a floating button that stays clickable during clickthrough mode"""
        self.floating_btn = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.floating_btn.setAttribute(Qt.WA_TranslucentBackground)
        self.floating_btn.setFixedSize(24, 24)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.floating_btn.setLayout(layout)

        self.floating_btn_inner = QPushButton("👆")
        self.floating_btn_inner.setFixedSize(24, 24)
        self.floating_btn_inner.setFocusPolicy(Qt.NoFocus)
        self.floating_btn_inner.setToolTip("Disable Clickthrough")
        self.floating_btn_inner.clicked.connect(self.toggle_clickthrough)
        layout.addWidget(self.floating_btn_inner)
        self.update_floating_button_style()

        self.floating_btn.hide()

    def update_floating_button_style(self):
        """Update floating button style to match main window background"""
        if hasattr(self, 'floating_btn_inner'):
            opacity = self.config.get('opacity', 0.9)
            alpha = max(int(opacity * 255), 3)
            self.floating_btn_inner.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(26, 26, 26, {alpha});
                    color: #44ff44;
                    border: none;
                    font-size: 10px;
                    padding: 5px;
                    margin: 0px;
                    outline: none;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.2);
                    color: #ffffff;
                    border: none;
                    border-radius: 3px;
                }}
                QPushButton:focus {{
                    outline: none;
                    border: none;
                }}
            """)

    def update_floating_button_position(self):
        """Update floating button position to match clickthrough button"""
        if self.floating_btn and self.clickthrough_btn:
            # Position exactly where the original clickthrough button is
            pos = self.clickthrough_btn.mapToGlobal(QPoint(0, 0))
            self.floating_btn.move(pos)

    # Mouse events for dragging
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # In clickthrough mode, draggable from title, progress bars, and labels
            # In normal mode, entire window is draggable (except buttons)
            if self.clickthrough_enabled:
                # Check if click is on any draggable element
                widget = self.childAt(event.pos())
                draggable_widgets = [
                    self.title_label, self.five_hour_progress_bg, self.five_hour_progress_fill,
                    self.five_hour_border_overlay, self.weekly_progress_bg,
                    self.weekly_progress_fill, self.weekly_border_overlay,
                    self.five_hour_usage_label, self.five_hour_reset_label,
                    self.five_hour_title, self.weekly_usage_label,
                    self.weekly_reset_label, self.weekly_title
                ]
                if widget in draggable_widgets:
                    self.dragging = True
                    self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                    event.accept()
            else:
                # Check if clicking on any button (buttons handle their own clicks)
                widget = self.childAt(event.pos())
                if not isinstance(widget, QPushButton):
                    self.dragging = True
                    self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                    event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.dragging:
            self.move(event.globalPos() - self.drag_position)
            # Update floating button position while dragging
            if self.floating_btn and self.floating_btn.isVisible():
                self.update_floating_button_position()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            # Save position
            self.config['position']['x'] = self.x()
            self.config['position']['y'] = self.y()
            self.save_config()
            # Update floating button position
            if self.floating_btn and self.floating_btn.isVisible():
                self.update_floating_button_position()
            event.accept()

    def toggle_clickthrough(self):
        """Toggle clickthrough mode - keeps opacity, optionally shows border"""
        self.clickthrough_enabled = not self.clickthrough_enabled

        # Enable/disable click-through using Windows API
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000

            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self.clickthrough_enabled:
                # Add transparent style (clicks pass through)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
            else:
                # Remove transparent style
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)
        except Exception as e:
            print(f"Click-through toggle error: {e}")

        if self.clickthrough_enabled:
            # Change button to indicate active state
            self.clickthrough_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 0, 0, 0.01);
                    color: #44ff44;
                    border: 1px solid transparent;
                    font-size: 12px;
                    padding: 5px;
                    margin: 0px;
                    outline: none;
                }
            """)
            self.clickthrough_btn.setToolTip("Disable Clickthrough")

            # Hide all buttons (floating button takes over)
            self.clickthrough_btn.hide()
            self.compact_btn.hide()
            self.refresh_btn.hide()
            self.settings_btn.hide()
            self.close_btn.hide()

            # Show floating button
            if self.floating_btn:
                self.update_floating_button_style()
                self.update_floating_button_position()
                self.floating_btn.show()
        else:
            # Restore normal button style
            self.clickthrough_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 0, 0, 0.01);
                    color: #aaaaaa;
                    border: none;
                    font-size: 14px;
                    padding: 5px;
                    margin: 0px;
                    outline: none;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.2);
                    color: #ffffff;
                    border: none;
                    border-radius: 3px;
                }
                QPushButton:focus {
                    outline: none;
                    border: none;
                }
            """)
            self.clickthrough_btn.setToolTip("Enable Clickthrough")

            # Show all buttons
            self.clickthrough_btn.show()
            self.compact_btn.show()
            self.refresh_btn.show()
            self.settings_btn.show()
            self.close_btn.show()

            # Hide floating button
            if self.floating_btn:
                self.floating_btn.hide()

        # Update progress bar widths
        QApplication.processEvents()
        if self.usage_data:
            five_hour = self.usage_data.get('five_hour', {})
            five_hour_utilization = five_hour.get('utilization', 0.0)
            max_width = self.five_hour_progress_bg.width()
            bar_width = int((five_hour_utilization / 100) * max_width)
            self.five_hour_progress_fill.setFixedWidth(bar_width)

            weekly = self.usage_data.get('seven_day', {})
            weekly_utilization = weekly.get('utilization', 0.0)
            weekly_max_width = self.weekly_progress_bg.width()
            weekly_bar_width = int((weekly_utilization / 100) * weekly_max_width)
            self.weekly_progress_fill.setFixedWidth(weekly_bar_width)

    def toggle_compact_mode(self):
        """Toggle compact mode"""
        self.config['compact_mode'] = not self.config.get('compact_mode', False)
        self.save_config()
        self.apply_compact_mode()

    def apply_compact_mode(self):
        """Apply or remove compact mode"""
        compact = self.config.get('compact_mode', False)

        # Reset all size constraints first
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)

        if compact:
            # Hide weekly, titles, and spacers (but keep five_hour_usage_label visible)
            self.five_hour_title.hide()
            self.five_hour_reset_label.hide()
            self.prediction_label.hide()
            self.spacer1.hide()
            self.separator.hide()
            self.spacer2.hide()
            self.weekly_title.hide()
            self.weekly_usage_label.hide()
            self.weekly_progress_bg.hide()
            self.weekly_reset_label.hide()
            self.five_hour_usage_label.show()
            self.compact_btn.setText("═")
            self.compact_btn.setToolTip("Expand")

            # Force layout recalculation and auto-size for compact
            self.content_frame.updateGeometry()
            self.main_frame.updateGeometry()
            self.centralWidget().updateGeometry()
            QApplication.processEvents()
            self.adjustSize()
            # Ensure width stays at 300
            self.setFixedSize(300, self.height())
        else:
            # Show everything including spacers
            self.five_hour_title.show()
            self.five_hour_usage_label.show()
            self.five_hour_reset_label.show()
            # prediction_label shows itself when prediction data exists
            self.spacer1.show()
            self.separator.show()
            self.spacer2.show()
            self.weekly_title.show()
            self.weekly_usage_label.show()
            self.weekly_progress_bg.show()
            self.weekly_reset_label.show()
            self.compact_btn.setText("─")
            self.compact_btn.setToolTip("Compact")

            # Use stored full height for non-compact, fixed width
            self.setFixedSize(300, self.full_height)

        # Trigger update
        QTimer.singleShot(0, self.update_progress)

    def manual_refresh(self):
        """Manually refresh usage data"""
        if self.clickthrough_enabled:
            return

        def refresh():
            data = self.fetch_usage_data()
            if data:
                self.usage_data = data
                # Update UI in main thread
                QTimer.singleShot(0, self.update_progress)

        threading.Thread(target=refresh, daemon=True).start()

    def show_settings_from_tray(self):
        """Show settings from tray - bypass clickthrough check"""
        self.show()
        self.is_hidden = False
        self.show_settings(from_tray=True)

    def show_settings(self, from_tray=False):
        """Show settings dialog"""
        if self.clickthrough_enabled and not from_tray:
            return

        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return

        self.settings_window = QDialog(self)
        self.settings_window.setWindowTitle("Settings")
        self.settings_window.setWindowFlags(self.settings_window.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.settings_window.setFixedSize(420, 750)
        self.settings_window.setStyleSheet("background-color: #1a1a1a;")
        self.apply_dark_titlebar(self.settings_window)

        # Position settings window - center if first time, else use saved position
        settings_pos = self.config.get('settings_position')
        if settings_pos:
            self.settings_window.move(settings_pos['x'], settings_pos['y'])
        else:
            # Center on screen
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - 420) // 2
            y = (screen.height() - 750) // 2
            self.settings_window.move(x, y)

        # Save position when closed
        def save_settings_position():
            pos = self.settings_window.pos()
            self.config['settings_position'] = {'x': pos.x(), 'y': pos.y()}
            self.save_config()
        self.settings_window.finished.connect(save_settings_position)

        layout = QVBoxLayout()
        self.settings_window.setLayout(layout)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background-color: #1a1a1a; }
            QScrollBar:vertical {
                background: transparent;
                width: 12px;
                margin: 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: #CC785C;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        layout.addWidget(scroll)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        scroll.setWidget(scroll_widget)

        label_style = "color: #cccccc; font-weight: bold; margin-top: 10px;"
        input_style = "background-color: #2a2a2a; color: #ffffff; border: 1px solid #444; padding: 5px;"
        checkbox_style = "color: #cccccc;"

        # Store references for saving
        self.settings_widgets = {}

        # --- System Section ---
        system_header = QLabel("System")
        system_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 5px;")
        scroll_layout.addWidget(system_header)

        # Auto-start checkbox
        auto_start_check = QCheckBox("Start with Windows")
        auto_start_check.setStyleSheet(checkbox_style)
        auto_start_check.setChecked(self.config.get('auto_start', False))
        def on_auto_start_change(state):
            enabled = bool(state)
            if self.set_auto_start(enabled):
                self.config['auto_start'] = enabled
                self.save_config()
            else:
                auto_start_check.setChecked(not enabled)
        auto_start_check.stateChanged.connect(on_auto_start_change)
        scroll_layout.addWidget(auto_start_check)

        # Start minimized checkbox
        start_min_check = QCheckBox("Start minimized to tray")
        start_min_check.setStyleSheet(checkbox_style)
        start_min_check.setChecked(self.config.get('start_minimized', False))
        start_min_check.setEnabled(TRAY_AVAILABLE)
        if not TRAY_AVAILABLE:
            start_min_check.setText("Start minimized (pystray required)")
        def on_start_min_change(state):
            self.config['start_minimized'] = bool(state)
            self.save_config()
        start_min_check.stateChanged.connect(on_start_min_change)
        scroll_layout.addWidget(start_min_check)

        # --- Appearance Section ---
        appearance_header = QLabel("Appearance")
        appearance_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 5px;")
        scroll_layout.addWidget(appearance_header)

        # Opacity slider
        current_opacity = int(self.config['opacity'] * 100)
        opacity_label = QLabel(f"Window Opacity: {current_opacity}%")
        opacity_label.setStyleSheet(label_style)
        scroll_layout.addWidget(opacity_label)

        opacity_slider = QSlider(Qt.Horizontal)
        opacity_slider.setMinimum(0)
        opacity_slider.setMaximum(100)
        opacity_slider.setValue(current_opacity)

        # Store reference for later
        text_bg_ref = {}

        def on_opacity_change(v):
            opacity_label.setText(f"Window Opacity: {v}%")
            self.config['opacity'] = v / 100
            self.apply_background_opacity()
            self.save_config()
            # Enable text background only when opacity is 0
            if 'checkbox' in text_bg_ref:
                text_bg_ref['checkbox'].setEnabled(v == 0)
                if v != 0 and text_bg_ref['checkbox'].isChecked():
                    text_bg_ref['checkbox'].setChecked(False)
        opacity_slider.valueChanged.connect(on_opacity_change)
        scroll_layout.addWidget(opacity_slider)
        self.settings_widgets['opacity'] = opacity_slider

        # Progress bar border checkbox
        border_check = QCheckBox("Show Progress Bar Border")
        border_check.setStyleSheet(checkbox_style)
        border_check.setChecked(self.config.get('show_border', False))
        def on_border_change(state):
            self.config['show_border'] = bool(state)
            self.save_config()
            self.apply_border()
        border_check.stateChanged.connect(on_border_change)
        scroll_layout.addWidget(border_check)
        self.settings_widgets['show_border'] = border_check

        # Text background checkbox (only enabled when opacity is 0)
        text_bg_check = QCheckBox("Show Text Background (requires 0% opacity)")
        text_bg_check.setStyleSheet(checkbox_style)
        text_bg_check.setChecked(self.config.get('text_background', False))
        text_bg_check.setEnabled(current_opacity == 0)
        text_bg_ref['checkbox'] = text_bg_check  # Store reference for opacity slider
        def on_text_bg_change(state):
            self.config['text_background'] = bool(state)
            self.save_config()
            self.apply_text_backgrounds()
            # Enable/disable opacity slider based on checkbox state
            if 'opacity_slider' in text_bg_ref:
                text_bg_ref['opacity_slider'].setEnabled(bool(state))
        text_bg_check.stateChanged.connect(on_text_bg_change)
        scroll_layout.addWidget(text_bg_check)

        # Text background opacity slider
        text_bg_opacity_layout = QHBoxLayout()
        text_bg_opacity_label = QLabel("Text BG Opacity:")
        text_bg_opacity_label.setStyleSheet("color: #cccccc;")
        text_bg_opacity_layout.addWidget(text_bg_opacity_label)

        text_bg_opacity_slider = QSlider(Qt.Horizontal)
        text_bg_opacity_slider.setRange(0, 100)
        text_bg_opacity_slider.setValue(self.config.get('text_background_opacity', 70))
        text_bg_opacity_slider.setEnabled(current_opacity == 0 and self.config.get('text_background', False))
        text_bg_opacity_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #CC785C; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::handle:horizontal:disabled { background: #666; }
        """)
        text_bg_opacity_layout.addWidget(text_bg_opacity_slider)

        text_bg_opacity_value = QLabel(f"{self.config.get('text_background_opacity', 70)}%")
        text_bg_opacity_value.setStyleSheet("color: #cccccc; min-width: 35px;")
        text_bg_opacity_layout.addWidget(text_bg_opacity_value)

        def on_text_bg_opacity_change(value):
            self.config['text_background_opacity'] = value
            text_bg_opacity_value.setText(f"{value}%")
            self.save_config()
            self.apply_text_backgrounds()
        text_bg_opacity_slider.valueChanged.connect(on_text_bg_opacity_change)
        scroll_layout.addLayout(text_bg_opacity_layout)

        # Store reference for enabling/disabling with checkbox
        text_bg_ref['opacity_slider'] = text_bg_opacity_slider

        # Dynamic bar color checkbox
        dynamic_color_check = QCheckBox("Dynamic bar color (yellow/red at high usage)")
        dynamic_color_check.setStyleSheet(checkbox_style)
        dynamic_color_check.setChecked(self.config.get('dynamic_bar_color', True))
        def on_dynamic_color_change(state):
            self.config['dynamic_bar_color'] = bool(state)
            self.save_config()
            if self.usage_data:
                self.update_progress()
        dynamic_color_check.stateChanged.connect(on_dynamic_color_change)
        scroll_layout.addWidget(dynamic_color_check)
        self.settings_widgets['text_background'] = text_bg_check

        # Show prediction checkbox
        prediction_check = QCheckBox("Show usage prediction")
        prediction_check.setStyleSheet(checkbox_style)
        prediction_check.setChecked(self.config.get('show_prediction', True))
        def on_prediction_change(state):
            self.config['show_prediction'] = bool(state)
            self.save_config()
            if self.usage_data:
                self.update_progress()
        prediction_check.stateChanged.connect(on_prediction_change)
        scroll_layout.addWidget(prediction_check)


        # Font size slider
        font_size_layout = QHBoxLayout()
        font_size_label = QLabel("Font Size:")
        font_size_label.setStyleSheet("color: #cccccc;")
        font_size_layout.addWidget(font_size_label)

        font_size_slider = QSlider(Qt.Horizontal)
        font_size_slider.setRange(10, 24)
        font_size_slider.setValue(self.config.get('font_size', 15))
        font_size_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #CC785C; width: 14px; margin: -4px 0; border-radius: 7px; }
        """)
        font_size_layout.addWidget(font_size_slider)

        font_size_value = QLabel(f"{self.config.get('font_size', 15)}px")
        font_size_value.setStyleSheet("color: #cccccc; min-width: 40px;")
        font_size_layout.addWidget(font_size_value)

        def on_font_size_change(value):
            self.config['font_size'] = value
            font_size_value.setText(f"{value}px")
            self.save_config()
            self.apply_font_size()
        font_size_slider.valueChanged.connect(on_font_size_change)
        scroll_layout.addLayout(font_size_layout)

        # Progress bar height slider
        bar_height_layout = QHBoxLayout()
        bar_height_label = QLabel("Progress Bar Height:")
        bar_height_label.setStyleSheet("color: #cccccc;")
        bar_height_layout.addWidget(bar_height_label)

        bar_height_slider = QSlider(Qt.Horizontal)
        bar_height_slider.setRange(4, 24)
        bar_height_slider.setValue(self.config.get('progress_bar_height', 12))
        bar_height_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #CC785C; width: 14px; margin: -4px 0; border-radius: 7px; }
        """)
        bar_height_layout.addWidget(bar_height_slider)

        bar_height_value = QLabel(f"{self.config.get('progress_bar_height', 12)}px")
        bar_height_value.setStyleSheet("color: #cccccc; min-width: 40px;")
        bar_height_layout.addWidget(bar_height_value)

        def on_bar_height_change(value):
            self.config['progress_bar_height'] = value
            bar_height_value.setText(f"{value}px")
            self.save_config()
            self.apply_progress_bar_height()
        bar_height_slider.valueChanged.connect(on_bar_height_change)
        scroll_layout.addLayout(bar_height_layout)

        # --- Notifications Section ---
        notif_header = QLabel("Notifications")
        notif_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 15px;")
        scroll_layout.addWidget(notif_header)

        # Enable notifications checkbox
        notif_enabled_check = QCheckBox("Enable usage notifications")
        notif_enabled_check.setStyleSheet(checkbox_style)
        notif_enabled_check.setChecked(self.config.get('notifications_enabled', True))
        scroll_layout.addWidget(notif_enabled_check)

        # Sound alerts checkbox
        sound_check = QCheckBox("Play sound with notifications")
        sound_check.setStyleSheet(checkbox_style)
        sound_check.setChecked(self.config.get('sound_alerts', False))
        def on_sound_change(state):
            self.config['sound_alerts'] = bool(state)
            self.save_config()
        sound_check.stateChanged.connect(on_sound_change)
        scroll_layout.addWidget(sound_check)

        # Sound type selection
        sound_layout = QHBoxLayout()
        sound_type_label = QLabel("Sound:")
        sound_type_label.setStyleSheet("color: #cccccc;")
        sound_layout.addWidget(sound_type_label)

        from PyQt5.QtWidgets import QComboBox, QFileDialog
        sound_combo = QComboBox()
        sound_combo.setStyleSheet("background: #2a2a2a; color: white; padding: 5px;")
        sound_combo.addItems(["Exclamation", "Hand", "Beep Low", "Beep High", "Double Beep", "Custom"])
        current_sound = self.config.get('sound_type', 'Exclamation')
        sound_combo.setCurrentText(current_sound)

        # Browse button for custom sound
        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet("background: #444; color: white; padding: 5px 10px;")
        browse_btn.setCursor(QCursor(Qt.PointingHandCursor))
        browse_btn.setVisible(current_sound == "Custom")

        def on_sound_type_change(text):
            self.config['sound_type'] = text
            self.save_config()
            browse_btn.setVisible(text == "Custom")
        sound_combo.currentTextChanged.connect(on_sound_type_change)
        sound_layout.addWidget(sound_combo)

        def browse_sound():
            file_path, _ = QFileDialog.getOpenFileName(
                self.settings_window,
                "Select Sound File",
                "",
                "Sound Files (*.wav *.mp3);;All Files (*)"
            )
            if file_path:
                self.config['custom_sound_path'] = file_path
                self.save_config()
        browse_btn.clicked.connect(browse_sound)
        sound_layout.addWidget(browse_btn)

        test_sound_btn = QPushButton("Test")
        test_sound_btn.setStyleSheet("background: #444; color: white; padding: 5px 10px;")
        test_sound_btn.setCursor(QCursor(Qt.PointingHandCursor))
        def test_sound():
            self.play_alert_sound()
        test_sound_btn.clicked.connect(test_sound)
        sound_layout.addWidget(test_sound_btn)
        scroll_layout.addLayout(sound_layout)

        # Volume slider
        volume_layout = QHBoxLayout()
        volume_label = QLabel("Volume:")
        volume_label.setStyleSheet("color: #cccccc;")
        volume_layout.addWidget(volume_label)

        volume_slider = QSlider(Qt.Horizontal)
        volume_slider.setRange(0, 100)
        volume_slider.setValue(self.config.get('sound_volume', 100))
        volume_slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #CC785C; width: 14px; margin: -4px 0; border-radius: 7px; }
        """)
        volume_layout.addWidget(volume_slider)

        volume_value = QLabel(f"{self.config.get('sound_volume', 100)}%")
        volume_value.setStyleSheet("color: #cccccc; min-width: 40px;")
        volume_layout.addWidget(volume_value)

        def on_volume_change(value):
            self.config['sound_volume'] = value
            volume_value.setText(f"{value}%")
            self.save_config()
        volume_slider.valueChanged.connect(on_volume_change)
        scroll_layout.addLayout(volume_layout)

        # Threshold list
        thresholds_label = QLabel("Alert thresholds:")
        thresholds_label.setStyleSheet("color: #cccccc;")
        scroll_layout.addWidget(thresholds_label)

        # Container for threshold items
        thresholds_container = QWidget()
        thresholds_layout = QVBoxLayout()
        thresholds_layout.setContentsMargins(0, 0, 0, 0)
        thresholds_layout.setSpacing(8)
        thresholds_container.setLayout(thresholds_layout)

        def refresh_thresholds():
            # Clear existing
            while thresholds_layout.count():
                item = thresholds_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # Add current thresholds as sliders
            thresholds = self.config.get('notification_thresholds', [70, 90])
            for i, threshold in enumerate(thresholds):
                row = QHBoxLayout()
                row.setSpacing(8)

                # Slider
                slider = QSlider(Qt.Horizontal)
                slider.setRange(1, 100)
                slider.setValue(threshold)
                slider.setStyleSheet("""
                    QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
                    QSlider::handle:horizontal { background: #CC785C; width: 14px; margin: -4px 0; border-radius: 7px; }
                """)
                row.addWidget(slider)

                # Value label
                value_label = QLabel(f"{threshold}%")
                value_label.setStyleSheet("color: #cccccc; min-width: 40px;")
                row.addWidget(value_label)

                # Update function for this slider
                def make_update_func(idx, lbl):
                    def update(val):
                        lbl.setText(f"{val}%")
                        thresholds = self.config.get('notification_thresholds', [70, 90])
                        old_val = thresholds[idx]
                        thresholds[idx] = val
                        self.config['notification_thresholds'] = thresholds
                        self.save_config()
                        # Update notified thresholds tracking
                        self.notified_thresholds.discard(f"5-hour_{old_val}")
                        self.notified_thresholds.discard(f"weekly_{old_val}")
                    return update
                slider.valueChanged.connect(make_update_func(i, value_label))

                # Remove button
                remove_btn = QPushButton("×")
                remove_btn.setFixedSize(20, 20)
                remove_btn.setStyleSheet("background: #ff4444; color: white; border: none; border-radius: 3px;")
                remove_btn.setCursor(QCursor(Qt.PointingHandCursor))
                remove_btn.clicked.connect(lambda checked, idx=i: remove_threshold(idx))
                row.addWidget(remove_btn)

                row_widget = QWidget()
                row_widget.setLayout(row)
                thresholds_layout.addWidget(row_widget)

        def remove_threshold(idx):
            thresholds = self.config.get('notification_thresholds', [70, 90])
            if 0 <= idx < len(thresholds):
                old_val = thresholds.pop(idx)
                self.config['notification_thresholds'] = thresholds
                self.save_config()
                self.notified_thresholds.discard(f"5-hour_{old_val}")
                self.notified_thresholds.discard(f"weekly_{old_val}")
                refresh_thresholds()

        refresh_thresholds()
        scroll_layout.addWidget(thresholds_container)

        # Add new threshold button
        add_btn = QPushButton("+ Add Alert")
        add_btn.setStyleSheet("background: #CC785C; color: white; border: none; padding: 8px 15px; border-radius: 3px;")
        add_btn.setCursor(QCursor(Qt.PointingHandCursor))
        def add_threshold():
            thresholds = self.config.get('notification_thresholds', [70, 90])
            # Find a value not already used
            new_val = 50
            while new_val in thresholds and new_val <= 100:
                new_val += 5
            if new_val > 100:
                new_val = 1
                while new_val in thresholds and new_val <= 100:
                    new_val += 1
            if new_val <= 100:
                thresholds.append(new_val)
                self.config['notification_thresholds'] = thresholds
                self.save_config()
                refresh_thresholds()
        add_btn.clicked.connect(add_threshold)
        scroll_layout.addWidget(add_btn)

        # Toggle visibility based on notifications enabled
        def update_notif_visibility(state):
            self.config['notifications_enabled'] = bool(state)
            self.save_config()
            thresholds_label.setVisible(bool(state))
            thresholds_container.setVisible(bool(state))
            add_btn.setVisible(bool(state))

        notif_enabled_check.stateChanged.connect(update_notif_visibility)
        # Set initial visibility
        notif_enabled = self.config.get('notifications_enabled', True)
        thresholds_label.setVisible(notif_enabled)
        thresholds_container.setVisible(notif_enabled)
        add_btn.setVisible(notif_enabled)

        # --- Colors Section ---
        colors_header = QLabel("Colors")
        colors_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 15px;")
        scroll_layout.addWidget(colors_header)

        # Color picker helper
        def get_text_color(bg_color):
            """Return black or white text depending on background luminance"""
            c = QColor(bg_color)
            luminance = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
            return "black" if luminance > 0.5 else "white"

        def create_color_btn(config_key, label_text, default_color):
            current = self.config.get(config_key, default_color)
            text_color = get_text_color(current)
            btn = QPushButton(f"{label_text}: {current}")
            btn.setStyleSheet(f"background-color: {current}; color: {text_color}; padding: 8px;")
            def pick():
                try:
                    dialog = QColorDialog(QColor(self.config.get(config_key, default_color)), self.settings_window)
                    dialog.setOption(QColorDialog.DontUseNativeDialog, True)
                    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
                    self.apply_dark_titlebar(dialog)
                    dialog.setStyleSheet("""
                        * { background-color: #1a1a1a; color: white; }
                        QLineEdit { background-color: #333; border: 1px solid #555; }
                        QSpinBox { background-color: #333; }
                    """)
                    # Style all buttons directly
                    for dialog_btn in dialog.findChildren(QPushButton):
                        dialog_btn.setStyleSheet("background-color: #444; color: white; border: 1px solid #555; padding: 5px 15px;")
                    dialog.exec_()
                    color = dialog.currentColor()
                    if color.isValid():
                        text_col = get_text_color(color.name())
                        btn.setText(f"{label_text}: {color.name()}")
                        btn.setStyleSheet(f"background-color: {color.name()}; color: {text_col}; padding: 8px;")
                        btn.setProperty('color_value', color.name())
                        self.config[config_key] = color.name()
                        # Apply color changes immediately
                        if config_key == 'five_hour_color':
                            self.five_hour_progress_fill.setStyleSheet(f"background-color: {color.name()}; border: none;")
                        elif config_key == 'weekly_color':
                            self.weekly_progress_fill.setStyleSheet(f"background-color: {color.name()}; border: none;")
                        elif config_key == 'border_color':
                            self.apply_border()
                        self.save_config()
                except Exception as e:
                    print(f"Color picker error: {e}")
            btn.clicked.connect(pick)
            btn.setProperty('color_value', current)
            return btn

        five_hour_btn = create_color_btn('five_hour_color', '5-Hour Bar', '#CC785C')
        scroll_layout.addWidget(five_hour_btn)
        self.settings_widgets['five_hour_color'] = five_hour_btn

        weekly_btn = create_color_btn('weekly_color', 'Weekly Bar', '#8B6BB7')
        scroll_layout.addWidget(weekly_btn)
        self.settings_widgets['weekly_color'] = weekly_btn

        border_btn = create_color_btn('border_color', 'Border Color', '#FFFFFF')
        scroll_layout.addWidget(border_btn)
        self.settings_widgets['border_color'] = border_btn

        # Warning colors (70% and 90%)
        warning_colors_label = QLabel("Warning Colors (for dynamic bar):")
        warning_colors_label.setStyleSheet("color: #aaaaaa; font-size: 11px; margin-top: 5px;")
        scroll_layout.addWidget(warning_colors_label)

        warning_70_btn = create_color_btn('warning_color_70', '70% Warning', '#ffaa44')
        scroll_layout.addWidget(warning_70_btn)

        warning_90_btn = create_color_btn('warning_color_90', '90% Warning', '#ff4444')
        scroll_layout.addWidget(warning_90_btn)

        # --- Behavior Section ---
        behavior_header = QLabel("Behavior")
        behavior_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 15px;")
        scroll_layout.addWidget(behavior_header)

        # Poll interval
        poll_label = QLabel("Update Interval (seconds)")
        poll_label.setStyleSheet(label_style)
        scroll_layout.addWidget(poll_label)

        poll_spin = QSpinBox()
        poll_spin.setStyleSheet(input_style)
        poll_spin.setMinimum(10)
        poll_spin.setMaximum(300)
        poll_spin.setValue(self.config['poll_interval'])
        def on_poll_change(v):
            self.config['poll_interval'] = v
            self.save_config()
        poll_spin.valueChanged.connect(on_poll_change)
        scroll_layout.addWidget(poll_spin)
        self.settings_widgets['poll_interval'] = poll_spin

        # Minimize to tray
        tray_check = QCheckBox("Minimize to System Tray")
        tray_check.setStyleSheet(checkbox_style)
        tray_check.setChecked(self.config.get('minimize_to_tray', False))
        tray_check.setEnabled(TRAY_AVAILABLE)
        if not TRAY_AVAILABLE:
            tray_check.setText("Minimize to System Tray (pystray not installed)")
        def on_tray_change(state):
            self.config['minimize_to_tray'] = bool(state)
            self.save_config()
        tray_check.stateChanged.connect(on_tray_change)
        scroll_layout.addWidget(tray_check)
        self.settings_widgets['minimize_to_tray'] = tray_check

        # Auto refresh session
        auto_refresh_check = QCheckBox("Auto Refresh Session")
        auto_refresh_check.setStyleSheet(checkbox_style)
        auto_refresh_check.setChecked(self.config.get('auto_refresh_session', False))
        def on_auto_refresh_change(state):
            self.config['auto_refresh_session'] = bool(state)
            self.save_config()
        auto_refresh_check.stateChanged.connect(on_auto_refresh_change)
        scroll_layout.addWidget(auto_refresh_check)
        self.settings_widgets['auto_refresh_session'] = auto_refresh_check

        # --- Hotkeys Section ---
        hotkeys_header = QLabel("Global Hotkeys")
        hotkeys_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 15px;")
        scroll_layout.addWidget(hotkeys_header)

        if not KEYBOARD_AVAILABLE:
            hotkey_warning = QLabel("Install 'keyboard' module: pip install keyboard")
            hotkey_warning.setStyleSheet("color: #ffaa44; font-size: 11px;")
            scroll_layout.addWidget(hotkey_warning)
        else:
            hotkey_info = QLabel("Click input and press keys (Esc/Backspace to clear)")
            hotkey_info.setStyleSheet("color: #777777; font-size: 11px;")
            scroll_layout.addWidget(hotkey_info)

        # Clickthrough hotkey
        hk_click_layout = QHBoxLayout()
        hk_click_layout.addStretch()
        hk_click_label = QLabel("Clickthrough:")
        hk_click_label.setStyleSheet("color: #cccccc;")
        hk_click_label.setFixedWidth(70)
        hk_click_layout.addWidget(hk_click_label)
        hk_click_input = HotkeyEdit()
        hk_click_input.setStyleSheet(input_style)
        hk_click_input.setFixedWidth(80)
        hk_click_input.setText(self.config.get('hotkey_clickthrough', 'ctrl+alt+c'))
        hk_click_input.setEnabled(KEYBOARD_AVAILABLE)
        def on_hk_click_change():
            self.config['hotkey_clickthrough'] = hk_click_input.text().strip()
            self.save_config()
            if KEYBOARD_AVAILABLE:
                self.setup_hotkeys()
        hk_click_input.textChanged.connect(on_hk_click_change)
        hk_click_layout.addWidget(hk_click_input)
        hk_click_layout.addStretch()
        scroll_layout.addLayout(hk_click_layout)

        # Compact hotkey
        hk_compact_layout = QHBoxLayout()
        hk_compact_layout.addStretch()
        hk_compact_label = QLabel("Compact:")
        hk_compact_label.setStyleSheet("color: #cccccc;")
        hk_compact_label.setFixedWidth(70)
        hk_compact_layout.addWidget(hk_compact_label)
        hk_compact_input = HotkeyEdit()
        hk_compact_input.setStyleSheet(input_style)
        hk_compact_input.setFixedWidth(80)
        hk_compact_input.setText(self.config.get('hotkey_compact', 'ctrl+alt+m'))
        hk_compact_input.setEnabled(KEYBOARD_AVAILABLE)
        def on_hk_compact_change():
            self.config['hotkey_compact'] = hk_compact_input.text().strip()
            self.save_config()
            if KEYBOARD_AVAILABLE:
                self.setup_hotkeys()
        hk_compact_input.textChanged.connect(on_hk_compact_change)
        hk_compact_layout.addWidget(hk_compact_input)
        hk_compact_layout.addStretch()
        scroll_layout.addLayout(hk_compact_layout)

        # Refresh hotkey
        hk_refresh_layout = QHBoxLayout()
        hk_refresh_layout.addStretch()
        hk_refresh_label = QLabel("Refresh:")
        hk_refresh_label.setStyleSheet("color: #cccccc;")
        hk_refresh_label.setFixedWidth(70)
        hk_refresh_layout.addWidget(hk_refresh_label)
        hk_refresh_input = HotkeyEdit()
        hk_refresh_input.setStyleSheet(input_style)
        hk_refresh_input.setFixedWidth(80)
        hk_refresh_input.setText(self.config.get('hotkey_refresh', 'ctrl+alt+r'))
        hk_refresh_input.setEnabled(KEYBOARD_AVAILABLE)
        def on_hk_refresh_change():
            self.config['hotkey_refresh'] = hk_refresh_input.text().strip()
            self.save_config()
            if KEYBOARD_AVAILABLE:
                self.setup_hotkeys()
        hk_refresh_input.textChanged.connect(on_hk_refresh_change)
        hk_refresh_layout.addWidget(hk_refresh_input)
        hk_refresh_layout.addStretch()
        scroll_layout.addLayout(hk_refresh_layout)

        # --- Session Section ---
        session_header = QLabel("Session")
        session_header.setStyleSheet("color: #CC785C; font-weight: bold; font-size: 16px; margin-top: 15px;")
        scroll_layout.addWidget(session_header)

        session_label = QLabel("Session Key")
        session_label.setStyleSheet(label_style)
        scroll_layout.addWidget(session_label)

        session_input = QLineEdit()
        session_input.setStyleSheet(input_style)
        session_input.setText(self.config.get('session_key', '') or '')
        session_input.setEchoMode(QLineEdit.Password)
        def on_session_change():
            key = session_input.text().strip()
            self.config['session_key'] = key if key else None
            self.save_config()
        session_input.editingFinished.connect(on_session_change)
        scroll_layout.addWidget(session_input)
        self.settings_widgets['session_key'] = session_input

        # Button row for session actions
        session_btn_layout = QHBoxLayout()
        scroll_layout.addLayout(session_btn_layout)

        # Show/hide session key button
        show_key_btn = QPushButton("Show Key")
        show_key_btn.setStyleSheet("background-color: #333; color: #aaa; padding: 5px;")
        def toggle_show_key():
            if session_input.echoMode() == QLineEdit.Password:
                session_input.setEchoMode(QLineEdit.Normal)
                show_key_btn.setText("Hide Key")
            else:
                session_input.setEchoMode(QLineEdit.Password)
                show_key_btn.setText("Show Key")
        show_key_btn.clicked.connect(toggle_show_key)
        session_btn_layout.addWidget(show_key_btn)

        # Clear key button
        clear_key_btn = QPushButton("Clear Key")
        clear_key_btn.setStyleSheet("background-color: #333; color: #aaa; padding: 5px;")
        def clear_key():
            session_input.clear()
            self.config['session_key'] = None
            self.save_config()
        clear_key_btn.clicked.connect(clear_key)
        session_btn_layout.addWidget(clear_key_btn)

        scroll_layout.addStretch()

        self.settings_window.show()

    def on_close(self):
        """Handle close button"""
        if self.clickthrough_enabled:
            return

        if TRAY_AVAILABLE and self.config.get('minimize_to_tray'):
            self.hide()
            self.is_hidden = True
        else:
            self.quit_app()

    def quit_app(self):
        """Quit the application"""
        self.polling_active = False
        if hasattr(self, 'monitor_timer'):
            self.monitor_timer.stop()
        if hasattr(self, 'polling_thread') and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=2)
        if self.tray_icon:
            self.tray_icon.stop()
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        QApplication.quit()

    def get_app_icon(self):
        """Get the app icon (orange circle)"""
        icon_pixmap = QPixmap(64, 64)
        icon_pixmap.fill(Qt.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor("#CC785C")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 56, 56)
        painter.end()
        return QIcon(icon_pixmap)

    def apply_dark_titlebar(self, window):
        """Apply dark title bar and app icon to a window"""
        window.setWindowIcon(self.get_app_icon())

        # Enable dark title bar on Windows
        try:
            hwnd = int(window.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(ctypes.c_int(1)), 4)
        except:
            pass

    def show_message(self, title, text, msg_type="info"):
        """Show a dark-themed message box"""
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setWindowFlags(msg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msg.setStyleSheet("""
            QMessageBox { background-color: #1a1a1a; }
            QMessageBox QLabel { color: #cccccc; }
            QPushButton { background-color: #333; color: white; padding: 5px 15px; border: 1px solid #555; }
            QPushButton:hover { background-color: #444; }
        """)
        if msg_type == "warning":
            msg.setIcon(QMessageBox.Warning)
        elif msg_type == "error":
            msg.setIcon(QMessageBox.Critical)
        else:
            msg.setIcon(QMessageBox.Information)
        self.apply_dark_titlebar(msg)
        msg.exec_()

    def show_login_dialog(self):
        """Show login dialog"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Login Required")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setStyleSheet("background-color: #1a1a1a;")
        self.apply_dark_titlebar(dialog)

        # Main widget with fixed size
        main_widget = QWidget()
        main_widget.setFixedSize(420, 240)

        wrapper_layout = QVBoxLayout()
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        dialog.setLayout(wrapper_layout)
        wrapper_layout.addWidget(main_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(15)
        main_widget.setLayout(layout)

        title = QLabel("🔐 Sign in to Claude")
        title.setStyleSheet("color: #CC785C; font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        title.setFixedHeight(25)
        layout.addWidget(title)

        # Status label (replaces info)
        status_label = QLabel("A browser window will open for login")
        status_label.setStyleSheet("color: #999999; font-size: 13px;")
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setWordWrap(True)
        status_label.setFixedHeight(40)
        layout.addWidget(status_label)

        layout.addStretch()

        # Sign In button
        signin_btn = QPushButton("Sign In")
        signin_btn.setFixedHeight(50)
        signin_btn.setStyleSheet("""
            QPushButton {
                background-color: #CC785C;
                color: white;
                padding: 12px;
                font-weight: bold;
                font-size: 16px;
                border: none;
            }
            QPushButton:hover {
                background-color: #D88B6F;
            }
            QPushButton:disabled {
                background-color: #666;
            }
        """)
        signin_btn.setEnabled(CHROMEDRIVER_AVAILABLE)
        logging.info(f"Sign In button enabled: {CHROMEDRIVER_AVAILABLE}")
        if not CHROMEDRIVER_AVAILABLE:
            signin_btn.setToolTip("Install undetected-chromedriver: pip install undetected-chromedriver setuptools")
            status_label.setText("⚠ Missing dependency: undetected-chromedriver")
            status_label.setStyleSheet("color: #ffaa44; font-size: 13px;")

        def update_status(text, color="#999999"):
            status_label.setText(text)
            status_label.setStyleSheet(f"color: {color}; font-size: 13px;")
            QApplication.processEvents()

        def on_signin():
            logging.info("Sign In button clicked")
            signin_btn.setEnabled(False)
            cancel_btn.setEnabled(False)
            update_status("Launching browser...")
            QApplication.processEvents()

            session_key = self.auto_grab_session_key(update_status)
            logging.info(f"auto_grab_session_key returned: {'key found' if session_key else 'None'}")
            if session_key:
                self.config['session_key'] = session_key
                self.save_config()
                update_status("✓ Login successful!", "#44ff44")
                QTimer.singleShot(500, dialog.accept)
                QTimer.singleShot(500, self.start_polling)
            else:
                logging.info("Re-enabling login dialog")
                signin_btn.setEnabled(True)
                cancel_btn.setEnabled(True)
                dialog.raise_()
                dialog.activateWindow()

        signin_btn.clicked.connect(on_signin)
        layout.addWidget(signin_btn)

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #cccccc;
                padding: 8px;
                font-size: 13px;
                border: none;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(cancel_btn)

        dialog.setFixedSize(main_widget.size())

        result = dialog.exec_()
        if result == QDialog.Rejected and not self.config.get('session_key'):
            self.quit_app()

    def auto_grab_session_key(self, update_status=None):
        """Launch browser to grab session key automatically"""
        logging.info("auto_grab_session_key called")

        def status(text, color="#999999"):
            if update_status:
                update_status(text, color)

        if not CHROMEDRIVER_AVAILABLE:
            logging.warning("CHROMEDRIVER_AVAILABLE is False")
            status("⚠ Missing dependency", "#ffaa44")
            return None

        if self.login_in_progress:
            logging.warning("login_in_progress is True, returning")
            return None

        self.login_in_progress = True
        session_key = None

        try:
            logging.info("Launching Chrome")
            status("Launching browser...")

            # Launch undetected Chrome (same as original)
            options = uc.ChromeOptions()
            options.add_argument('--start-maximized')

            self.driver = uc.Chrome(options=options, use_subprocess=True)
            logging.info("Chrome launched, navigating to claude.ai")
            self.driver.get('https://claude.ai')

            status("Waiting for login...")

            # Wait for login (check for sessionKey cookie)
            max_wait = 300  # 5 minutes
            start_time = time.time()
            all_cookies = None

            logging.info("Waiting for sessionKey cookie...")
            while time.time() - start_time < max_wait:
                try:
                    cookies = self.driver.get_cookies()
                    for cookie in cookies:
                        if cookie.get('name') == 'sessionKey':
                            session_key = cookie.get('value')
                            all_cookies = cookies
                            logging.info("sessionKey cookie found!")
                            break
                    if session_key:
                        break
                except Exception:
                    # Browser was closed manually
                    logging.info("Browser closed by user")
                    self.driver = None
                    status("Browser closed", "#ffaa44")
                    return None
                time.sleep(2)  # Poll every 2 seconds like original

            logging.info("Closing browser")
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None

            if session_key and all_cookies:
                # Save all cookies as cookie string (like original)
                cookie_string = '; '.join([f"{c['name']}={c['value']}" for c in all_cookies])
                self.config['cookie_string'] = cookie_string
                logging.info("Success - session key and cookies captured")
            else:
                logging.warning("Timeout - no session key found")
                status("Login timed out", "#ff4444")

        except Exception as e:
            logging.error(f"Exception in auto_grab_session_key: {e}", exc_info=True)
            status("Failed to launch browser", "#ff4444")
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None
        finally:
            self.login_in_progress = False

        return session_key

    def fetch_usage_data(self, retry_attempt=0):
        """Fetch usage data from Claude API with retry logic"""
        if not self.config.get('session_key'):
            self.api_status = 'error'
            self.last_api_error = 'No session key'
            return None

        max_retries = 3
        base_delay = 2

        try:
            if retry_attempt > 0:
                self.api_status = 'warning'
                QTimer.singleShot(0, self.update_api_status_ui)

            try:
                import cloudscraper
            except ImportError:
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "cloudscraper"])
                import cloudscraper

            scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
            )

            cookie_string = self.config.get('cookie_string', f'sessionKey={self.config["session_key"]}')

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://claude.ai/chats',
            }

            for cookie_pair in cookie_string.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    scraper.cookies.set(name, value, domain='claude.ai')

            response = scraper.get('https://claude.ai/api/organizations', headers=headers, timeout=15)

            if response.status_code == 200:
                orgs = response.json()
                if orgs and len(orgs) > 0:
                    org_id = orgs[0].get('uuid')
                    usage_response = scraper.get(
                        f'https://claude.ai/api/organizations/{org_id}/usage',
                        headers=headers, timeout=15
                    )
                    if usage_response.status_code == 200:
                        self.api_status = 'ok'
                        self.last_api_error = None
                        QTimer.singleShot(0, self.update_api_status_ui)
                        return usage_response.json()

            if retry_attempt < max_retries:
                time.sleep(base_delay * (2 ** retry_attempt))
                return self.fetch_usage_data(retry_attempt + 1)

            self.api_status = 'error'
            QTimer.singleShot(0, self.update_api_status_ui)
            return None

        except Exception as e:
            logging.error(f"Fetch error: {type(e).__name__}: {e}")
            if retry_attempt < max_retries:
                time.sleep(base_delay * (2 ** retry_attempt))
                return self.fetch_usage_data(retry_attempt + 1)
            self.api_status = 'error'
            return None

    def update_api_status_ui(self):
        """Update API status dot color"""
        colors = {'ok': '#44ff44', 'warning': '#ffaa44', 'error': '#ff4444', 'unknown': '#888888'}
        self.api_status_dot.setStyleSheet(f"color: {colors.get(self.api_status, '#888888')}; background: transparent; font-size: 12px; padding: 0px; margin: 0px;")

    def format_time_remaining(self, time_left_seconds):
        """Format time remaining"""
        if time_left_seconds <= 0:
            return "Resetting soon..."
        hours = int(time_left_seconds // 3600)
        minutes = int((time_left_seconds % 3600) // 60)
        seconds = int(time_left_seconds % 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def calculate_prediction(self, current_utilization):
        """Calculate time until 100% based on usage rate"""
        now = time.time()

        # Keep only last 30 minutes of data (filter before append to avoid memory spike)
        cutoff = now - (30 * 60)
        self.usage_history = [(t, u) for t, u in self.usage_history if t > cutoff]

        # Add current reading to history
        self.usage_history.append((now, current_utilization))

        # Need at least 2 data points spread over 2+ minutes
        if len(self.usage_history) < 2:
            return None

        oldest_time, oldest_usage = self.usage_history[0]
        time_diff = now - oldest_time

        if time_diff < 120:  # Less than 2 minutes
            return None

        usage_diff = current_utilization - oldest_usage

        if usage_diff <= 0:  # Usage not increasing
            return None

        # Rate in % per second
        rate = usage_diff / time_diff

        # Time to reach 100%
        remaining_usage = 100 - current_utilization
        if remaining_usage <= 0:
            return None

        seconds_to_100 = remaining_usage / rate

        if seconds_to_100 > 18000:  # More than 5 hours
            return None

        return seconds_to_100

    def update_progress(self):
        """Update UI with latest usage data"""
        if not self.usage_data:
            return

        try:
            from dateutil import parser as date_parser

            five_hour = self.usage_data.get('five_hour', {})
            five_hour_utilization = five_hour.get('utilization', 0.0)
            five_hour_resets_at = five_hour.get('resets_at')

            compact_reset_text = ""
            if self.config.get('compact_mode', False) and five_hour_resets_at:
                try:
                    reset_time = date_parser.parse(five_hour_resets_at)
                    now = datetime.now(reset_time.tzinfo)
                    time_left = reset_time - now
                    if time_left.total_seconds() > 0:
                        time_str = self.format_time_remaining(time_left.total_seconds())
                        compact_reset_text = f" • Resets: {time_str}"
                except:
                    pass

            if self.config.get('compact_mode', False):
                self.five_hour_usage_label.setText(f"5h: {five_hour_utilization:.1f}% used{compact_reset_text}")
            else:
                self.five_hour_usage_label.setText(f"{five_hour_utilization:.1f}% used")

            # Calculate fill width
            max_width = self.five_hour_progress_bg.width()
            bar_width = int((five_hour_utilization / 100) * max_width)
            self.five_hour_progress_fill.setFixedWidth(bar_width)

            if self.config.get('dynamic_bar_color', True):
                if five_hour_utilization >= 90:
                    color = self.config.get('warning_color_90', '#ff4444')
                elif five_hour_utilization >= 70:
                    color = self.config.get('warning_color_70', '#ffaa44')
                else:
                    color = self.config.get('five_hour_color', '#CC785C')
            else:
                color = self.config.get('five_hour_color', '#CC785C')
            self.five_hour_progress_fill.setStyleSheet(f"background-color: {color}; border: none;")

            if five_hour_resets_at and not self.config.get('compact_mode', False):
                try:
                    reset_time = date_parser.parse(five_hour_resets_at)
                    now = datetime.now(reset_time.tzinfo)
                    time_left = reset_time - now
                    if time_left.total_seconds() > 0:
                        self.five_hour_reset_label.setText(f"Resets in: {self.format_time_remaining(time_left.total_seconds())}")
                except:
                    pass

            # Update prediction
            if not self.config.get('compact_mode', False) and self.config.get('show_prediction', True):
                prediction = self.calculate_prediction(five_hour_utilization)
                if prediction:
                    pred_text = f"→ 100% in ~{self.format_time_remaining(prediction)}"
                    self.prediction_label.setText(pred_text)
                else:
                    self.prediction_label.setText("→ 100% in ~—")
                self.prediction_label.show()
            else:
                self.prediction_label.hide()

            weekly = self.usage_data.get('seven_day', {})
            weekly_utilization = weekly.get('utilization', 0.0)
            weekly_resets_at = weekly.get('resets_at')

            # Only update weekly labels if not in compact mode (they're hidden in compact mode)
            if not self.config.get('compact_mode', False):
                self.weekly_usage_label.setText(f"{weekly_utilization:.1f}% used")

                if weekly_resets_at:
                    try:
                        reset_time = date_parser.parse(weekly_resets_at)
                        now = datetime.now(reset_time.tzinfo)
                        time_left = reset_time - now
                        if time_left.total_seconds() > 0:
                            self.weekly_reset_label.setText(f"Resets in: {self.format_time_remaining(time_left.total_seconds())}")
                    except:
                        pass

            # Calculate weekly fill width
            weekly_max_width = self.weekly_progress_bg.width()
            weekly_bar_width = int((weekly_utilization / 100) * weekly_max_width)
            self.weekly_progress_fill.setFixedWidth(weekly_bar_width)

            if self.config.get('dynamic_bar_color', True):
                if weekly_utilization >= 90:
                    color = self.config.get('warning_color_90', '#ff4444')
                elif weekly_utilization >= 70:
                    color = self.config.get('warning_color_70', '#ffaa44')
                else:
                    color = self.config.get('weekly_color', '#8B6BB7')
            else:
                color = self.config.get('weekly_color', '#8B6BB7')
            self.weekly_progress_fill.setStyleSheet(f"background-color: {color}; border: none;")

            # Check for notification thresholds
            self.check_and_notify(five_hour_utilization, "5-hour")
            self.check_and_notify(weekly_utilization, "weekly")
            self.initial_thresholds_set = True  # Enable notifications after first check

            # Update tray tooltip
            self.update_tray_tooltip()

        except Exception as e:
            self.five_hour_usage_label.setText("Error")
            if not self.config.get('compact_mode', False):
                self.weekly_usage_label.setText("Error")

    def start_polling(self):
        """Start background polling"""
        logging.info("start_polling called")
        self.polling_active = True

        def poll_loop():
            logging.info("poll_loop started")
            while self.polling_active:
                logging.info("Fetching usage data...")
                data = self.fetch_usage_data()
                logging.info(f"Fetch result: {bool(data)}")
                if data:
                    self.usage_data = data
                    QTimer.singleShot(0, self.update_progress)
                time.sleep(self.config['poll_interval'])

        self.polling_thread = threading.Thread(target=poll_loop, daemon=True)
        self.polling_thread.start()

        def initial_fetch():
            time.sleep(0.5)
            data = self.fetch_usage_data()
            if data:
                self.usage_data = data
                QTimer.singleShot(0, self.update_progress)

        threading.Thread(target=initial_fetch, daemon=True).start()

    def check_and_notify(self, utilization, limit_type="5-hour"):
        """Check if utilization crossed any notification thresholds"""
        if not self.config.get('notifications_enabled', True):
            return

        thresholds = self.config.get('notification_thresholds', [70, 90])
        for threshold in sorted(thresholds):
            key = f"{limit_type}_{threshold}"
            if utilization >= threshold and key not in self.notified_thresholds:
                self.notified_thresholds.add(key)
                # Only send notification if not first fetch (avoid re-notifying on restart)
                if self.initial_thresholds_set:
                    self.send_notification(
                        f"Claude {limit_type} Usage Alert",
                        f"You've reached {threshold}% of your {limit_type} limit ({utilization:.1f}% used)"
                    )
            elif utilization < threshold and key in self.notified_thresholds:
                # Reset notification if usage drops below threshold
                self.notified_thresholds.discard(key)

    def send_notification(self, title, message):
        """Send a system notification"""
        if self.tray_icon and TRAY_AVAILABLE:
            try:
                self.tray_icon.notify(title, message)
            except Exception as e:
                logging.error(f"Notification error: {e}")

        # Play sound alert if enabled
        if self.config.get('sound_alerts', False):
            self.play_alert_sound()

    def play_alert_sound(self):
        """Play system alert sound"""
        try:
            import winsound
            sound_type = self.config.get('sound_type', 'Exclamation')

            if sound_type == 'Exclamation':
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            elif sound_type == 'Hand':
                winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC)
            elif sound_type == 'Beep Low':
                winsound.Beep(400, 300)
            elif sound_type == 'Beep High':
                winsound.Beep(800, 300)
            elif sound_type == 'Double Beep':
                winsound.Beep(600, 150)
                winsound.Beep(800, 150)
            elif sound_type == 'Custom':
                custom_path = self.config.get('custom_sound_path', '')
                if custom_path and os.path.exists(custom_path):
                    if custom_path.lower().endswith('.mp3'):
                        self.play_mp3(custom_path)
                    else:
                        self.play_wav_with_volume(custom_path)
                else:
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception as e:
            logging.error(f"Sound error: {e}")

    def play_mp3(self, filepath):
        """Play MP3 using Windows MCI"""
        try:
            winmm = ctypes.windll.winmm
            # Close any previous sound
            winmm.mciSendStringW('close mp3_sound', None, 0, None)
            # Open file
            winmm.mciSendStringW(f'open "{filepath}" type mpegvideo alias mp3_sound', None, 0, None)
            # Set volume (0-1000 scale)
            volume = self.config.get('sound_volume', 100) * 10
            winmm.mciSendStringW(f'setaudio mp3_sound volume to {volume}', None, 0, None)
            # Play
            winmm.mciSendStringW('play mp3_sound', None, 0, None)
        except Exception as e:
            logging.error(f"MP3 error: {e}")

    def play_wav_with_volume(self, filepath):
        """Play WAV using Windows MCI with volume control"""
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW('close wav_sound', None, 0, None)
            winmm.mciSendStringW(f'open "{filepath}" type waveaudio alias wav_sound', None, 0, None)
            volume = self.config.get('sound_volume', 100) * 10
            winmm.mciSendStringW(f'setaudio wav_sound volume to {volume}', None, 0, None)
            winmm.mciSendStringW('play wav_sound', None, 0, None)
        except Exception as e:
            logging.error(f"WAV error: {e}")

    def set_auto_start(self, enabled):
        """Enable or disable auto-start on Windows boot"""
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "ClaudeUsage"

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)

            if enabled:
                # Get the path to the current script/exe
                if getattr(sys, 'frozen', False):
                    # Running as compiled exe
                    app_path = sys.executable
                else:
                    # Running as script
                    app_path = f'pythonw "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass  # Key doesn't exist, nothing to delete

            winreg.CloseKey(key)
            return True
        except Exception as e:
            logging.error(f"Auto-start error: {e}")
            return False

    def _start_minimized(self):
        """Hide window to tray at startup"""
        self.hide()
        self.is_hidden = True

    def setup_hotkeys(self):
        """Setup global hotkeys"""
        if not KEYBOARD_AVAILABLE:
            logging.warning("Keyboard module not available")
            return

        try:
            # Clear any existing hotkeys
            try:
                keyboard.unhook_all_hotkeys()
            except:
                pass

            # Register hotkeys
            hk_click = self.config.get('hotkey_clickthrough', 'ctrl+alt+c')
            hk_compact = self.config.get('hotkey_compact', 'ctrl+alt+m')
            hk_refresh = self.config.get('hotkey_refresh', 'ctrl+alt+r')

            if hk_click:
                keyboard.add_hotkey(hk_click, self._on_hotkey_clickthrough, suppress=False)
                logging.info(f"Registered hotkey: {hk_click}")
            if hk_compact:
                keyboard.add_hotkey(hk_compact, self._on_hotkey_compact, suppress=False)
                logging.info(f"Registered hotkey: {hk_compact}")
            if hk_refresh:
                keyboard.add_hotkey(hk_refresh, self._on_hotkey_refresh, suppress=False)
                logging.info(f"Registered hotkey: {hk_refresh}")

        except Exception as e:
            logging.error(f"Hotkey setup error: {e}")

    def _on_hotkey_clickthrough(self):
        """Handle clickthrough hotkey"""
        self.hotkey_clickthrough_signal.emit()

    def _on_hotkey_compact(self):
        """Handle compact hotkey"""
        self.hotkey_compact_signal.emit()

    def _on_hotkey_refresh(self):
        """Handle refresh hotkey"""
        self.hotkey_refresh_signal.emit()

    def update_tray_tooltip(self):
        """Update the tray icon tooltip with current usage"""
        if self.tray_icon and self.usage_data:
            five_hour = self.usage_data.get('five_hour', {}).get('utilization', 0)
            weekly = self.usage_data.get('seven_day', {}).get('utilization', 0)
            tooltip = f"Claude Usage\n5h: {five_hour:.1f}% | Weekly: {weekly:.1f}%"
            self.tray_icon.title = tooltip

    def create_tray_icon(self):
        """Create system tray icon"""
        from PIL import Image, ImageDraw

        # Create a simple icon (orange circle on transparent background)
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, size-4, size-4], fill='#CC785C')

        def on_show(icon, item):
            self.show()
            self.is_hidden = False

        def on_toggle_clickthrough(icon, item):
            # Use QTimer to run in main thread
            QTimer.singleShot(0, self.toggle_clickthrough)

        def on_settings(icon, item):
            QTimer.singleShot(0, self.show_settings_from_tray)

        def on_quit(icon, item):
            icon.stop()
            QApplication.quit()

        menu = pystray.Menu(
            pystray.MenuItem("Show", on_show, default=True),
            pystray.MenuItem("Settings", on_settings),
            pystray.MenuItem("Toggle Clickthrough", on_toggle_clickthrough),
            pystray.MenuItem("Quit", on_quit)
        )

        self.tray_icon = pystray.Icon("Claude Usage", img, "Claude Usage", menu)

        # Run tray icon in separate thread
        import threading
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    # Remove focus rectangles globally
    app.setStyleSheet("""
        QPushButton:focus { outline: none; border: none; }
        QPushButton { outline: none; }
        *:focus { outline: none; }
    """)
    window = ClaudeUsageBar()
    window.show()
    sys.exit(app.exec_())
