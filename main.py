"""
Interview Assistant — Main entry point.
- Groq API (Llama 3.3 70B, free)
- Microphone listener           (Ctrl+Shift+A)
- System audio / Interviewer    (Ctrl+Shift+S) — WASAPI loopback
- Manual text input in overlay
- Device picker dialog for when auto-detect fails
- Crash logger → crash.log next to EXE
- Font size, language dropdown, resume context  (NEW)
"""

import sys
import os
import threading
import traceback
import logging

from PyQt5.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QAction,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QActionGroup,
    QMessageBox, QListWidget, QListWidgetItem
)
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject

from config import load_config, save_config, get_api_key, set_api_key, get_mode, set_mode
from overlay import OverlayWindow
from screen_capture import scan_screen
from audio_listener import AudioListener, SystemAudioListener
from solver import process_text, is_question, solve_with_claude, solve_streaming

import keyboard


# ── Crash logger ──────────────────────────────────────────────────────────────

def _setup_logging():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base, "crash.log")
    logging.basicConfig(
        filename=log_path, level=logging.ERROR,
        format="%(asctime)s  %(levelname)s  %(message)s",
    )

def _global_exception_handler(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.error("UNHANDLED EXCEPTION\n%s", msg)
    try:
        app = QApplication.instance()
        if app:
            QMessageBox.critical(
                None, "Interview Assistant — Error",
                f"Error saved to crash.log\n\n{str(exc_value)}"
            )
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


# ── API Key Dialog ────────────────────────────────────────────────────────────

class ApiKeyDialog(QDialog):
    def __init__(self, existing_key=""):
        super().__init__()
        self.setWindowTitle("Interview Assistant — Setup")
        self.setFixedWidth(500)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._build_ui(existing_key)

    def _build_ui(self, existing_key):
        self.setStyleSheet("""
            QDialog { background-color: #1e1e2e; }
            QLabel { color: #cdd6f4; background: transparent; font-family: 'Segoe UI'; }
            QLabel#title { color: #cba6f7; font-size: 22px; font-weight: bold; }
            QLabel#sub   { color: #a6adc8; font-size: 12px; }
            QLabel#desc  { color: #cdd6f4; font-size: 12px; }
            QLabel#link  { color: #89b4fa; font-size: 11px; }
            QLineEdit {
                background: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 8px;
                padding: 10px 14px; font-size: 13px; font-family: 'Segoe UI';
            }
            QLineEdit:focus { border: 1px solid #cba6f7; }
            QPushButton#start {
                background: #cba6f7; color: #1e1e2e; border: none;
                border-radius: 8px; padding: 11px 28px;
                font-size: 13px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton#start:hover    { background: #d4b8ff; }
            QPushButton#start:disabled { background: #45475a; color: #6c7086; }
            QPushButton#exit {
                background: transparent; color: #6c7086;
                border: 1px solid #45475a; border-radius: 8px;
                padding: 11px 20px; font-size: 13px; font-family: 'Segoe UI';
            }
            QPushButton#exit:hover { color: #a6adc8; border-color: #6c7086; }
            QPushButton#toggle {
                background: transparent; color: #89b4fa; border: none;
                font-size: 11px; font-family: 'Segoe UI'; text-align: left; padding: 0;
            }
            QPushButton#toggle:hover { color: #cba6f7; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 32)
        lay.setSpacing(0)

        h = QHBoxLayout(); h.setSpacing(14)
        ico = QLabel("🎯"); ico.setFont(QFont("Segoe UI", 28))
        ico.setFixedSize(52, 52); ico.setAlignment(Qt.AlignCenter)
        h.addWidget(ico)
        t = QVBoxLayout(); t.setSpacing(2)
        title = QLabel("Interview Assistant"); title.setObjectName("title")
        sub   = QLabel("Your invisible AI co-pilot — Powered by Groq (Free)")
        sub.setObjectName("sub")
        t.addWidget(title); t.addWidget(sub)
        h.addLayout(t); h.addStretch()
        lay.addLayout(h); lay.addSpacing(24)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: #313244; border: none; max-height: 1px;")
        lay.addWidget(sep); lay.addSpacing(22)

        desc = QLabel(
            "Paste your <b>Groq API key</b> below to get started.<br>"
            "Llama 3.3 70B (100% free) answers interview questions in real time."
        )
        desc.setObjectName("desc"); desc.setWordWrap(True)
        desc.setTextFormat(Qt.RichText); lay.addWidget(desc)
        lay.addSpacing(8)

        link = QLabel('👉 Get your FREE key at '
            '<a href="https://console.groq.com/keys" style="color:#89b4fa;">console.groq.com/keys</a>')
        link.setObjectName("link"); link.setOpenExternalLinks(True)
        lay.addWidget(link); lay.addSpacing(22)

        key_lbl = QLabel("Groq API Key")
        key_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        key_lbl.setObjectName("desc"); lay.addWidget(key_lbl)
        lay.addSpacing(6)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.key_input.setText(existing_key)
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.textChanged.connect(
            lambda t: self.start_btn.setEnabled(bool(t.strip()))
        )
        lay.addWidget(self.key_input); lay.addSpacing(6)

        toggle_lay = QHBoxLayout()
        self.show_btn = QPushButton("👁 Show key")
        self.show_btn.setObjectName("toggle")
        self.show_btn.setCursor(Qt.PointingHandCursor)
        self._key_visible = False
        self.show_btn.clicked.connect(self._toggle_visibility)
        toggle_lay.addWidget(self.show_btn); toggle_lay.addStretch()
        lay.addLayout(toggle_lay); lay.addSpacing(30)

        btn_lay = QHBoxLayout(); btn_lay.setSpacing(12)
        exit_btn = QPushButton("Exit"); exit_btn.setObjectName("exit")
        exit_btn.setCursor(Qt.PointingHandCursor)
        exit_btn.clicked.connect(self.reject); btn_lay.addWidget(exit_btn)
        btn_lay.addStretch()

        self.start_btn = QPushButton("  Start Interview Assistant  →")
        self.start_btn.setObjectName("start")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setEnabled(bool(existing_key.strip()))
        self.start_btn.clicked.connect(self._on_start)
        btn_lay.addWidget(self.start_btn); lay.addLayout(btn_lay)
        self.key_input.returnPressed.connect(self.start_btn.click)

    def _toggle_visibility(self):
        self._key_visible = not self._key_visible
        self.key_input.setEchoMode(
            QLineEdit.Normal if self._key_visible else QLineEdit.Password
        )
        self.show_btn.setText("🙈 Hide key" if self._key_visible else "👁 Show key")

    def _on_start(self):
        key = self.key_input.text().strip()
        if key:
            set_api_key(key); self.accept()


# ── Device Picker Dialog ──────────────────────────────────────────────────────

class DevicePickerDialog(QDialog):
    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select System Audio Device")
        self.setFixedWidth(520)
        self.setMinimumHeight(300)
        self.selected_index = None
        self._build_ui(devices)

    def _build_ui(self, devices):
        self.setStyleSheet("""
            QDialog   { background: #1e1e2e; }
            QLabel    { color: #cdd6f4; font-family: 'Segoe UI'; font-size: 13px; background: transparent; }
            QLabel#h  { color: #cba6f7; font-size: 15px; font-weight: bold; }
            QLabel#s  { color: #a6adc8; font-size: 11px; }
            QListWidget {
                background: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 8px;
                font-family: 'Segoe UI'; font-size: 12px; padding: 4px;
            }
            QListWidget::item          { padding: 8px 10px; border-radius: 6px; }
            QListWidget::item:selected { background: #45475a; color: #cba6f7; }
            QPushButton#ok {
                background: #cba6f7; color: #1e1e2e; border: none;
                border-radius: 8px; padding: 10px 24px;
                font-size: 13px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton#ok:hover    { background: #d4b8ff; }
            QPushButton#ok:disabled { background: #45475a; color: #6c7086; }
            QPushButton#cancel {
                background: transparent; color: #6c7086;
                border: 1px solid #45475a; border-radius: 8px;
                padding: 10px 20px; font-size: 13px; font-family: 'Segoe UI';
            }
            QPushButton#cancel:hover { color: #a6adc8; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        h = QLabel("🔊  Select Audio Device for Interviewer Voice"); h.setObjectName("h")
        lay.addWidget(h)
        s = QLabel(
            "Choose a loopback / stereo mix device to capture what plays through your speakers.\n"
            "Items marked [LOOPBACK] are best. Try them one by one if unsure."
        )
        s.setObjectName("s"); s.setWordWrap(True); lay.addWidget(s)

        self.list_widget = QListWidget()
        self._device_map = {}
        for row, (dev_idx, name, is_loopback, rate) in enumerate(devices):
            tag  = "  [LOOPBACK] " if is_loopback else "  "
            item = QListWidgetItem(f"{tag}{name}  ({rate} Hz)")
            self.list_widget.addItem(item)
            self._device_map[row] = dev_idx
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.itemDoubleClicked.connect(self._on_ok)
        lay.addWidget(self.list_widget)

        btn_lay = QHBoxLayout(); btn_lay.setSpacing(10)
        cancel_btn = QPushButton("Cancel"); cancel_btn.setObjectName("cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(cancel_btn); btn_lay.addStretch()
        self.ok_btn = QPushButton("Use This Device"); self.ok_btn.setObjectName("ok")
        self.ok_btn.setEnabled(self.list_widget.count() > 0)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_lay.addWidget(self.ok_btn)
        lay.addLayout(btn_lay)

    def _on_ok(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            self.selected_index = self._device_map.get(row); self.accept()


# ── Worker Signals ────────────────────────────────────────────────────────────

class WorkerSignals(QObject):
    show_answer   = pyqtSignal(str, str)
    show_status   = pyqtSignal(str)
    audio_text    = pyqtSignal(str)
    move_window   = pyqtSignal(int, int)
    resize_window = pyqtSignal(int, int)
    # Streaming signals — emitted token-by-token from the solver thread
    stream_start  = pyqtSignal(str)       # question
    stream_token  = pyqtSignal(str)       # one partial token
    stream_done   = pyqtSignal(str, str)  # question, full_answer


# ── Main Application ──────────────────────────────────────────────────────────

class MeetingAssistant:

    def __init__(self, app):
        self.app          = app
        self.config       = load_config()
        self.current_mode = self.config.get("interview_mode", "interview")
        self.signals      = WorkerSignals()
        self._sys_dev_index = self.config.get("system_audio_device_index", None)

        # NEW — language and resume context loaded from config
        self.current_language = self.config.get("language", "Auto-Detect")
        self.resume_text      = self.config.get("resume_text", "")

        # Overlay — pass persisted font size, language, resume
        self.overlay = OverlayWindow(
            display_seconds   = self.config.get("overlay_display_seconds", 30),
            initial_font_size = self.config.get("font_size", 12),
            initial_language  = self.current_language,
            initial_resume    = self.resume_text,
        )
        self.signals.show_answer.connect(self.overlay.show_answer)
        self.signals.show_status.connect(self.overlay.show_status)
        self.signals.move_window.connect(self.overlay.move_relative)
        self.signals.resize_window.connect(self.overlay.resize_relative)
        self.overlay.close_app_signal.connect(self._exit)
        self.overlay.manual_question_signal.connect(self._handle_manual_question)
        self.overlay.change_api_key_signal.connect(self._prompt_api_key)
        # Streaming connections
        self.signals.stream_start.connect(self.overlay.stream_start)
        self.signals.stream_token.connect(self.overlay.stream_append)
        self.signals.stream_done.connect(self.overlay.stream_finish)

        # NEW — wire up language, resume, font-size signals
        self.overlay.language_changed.connect(self._on_language_changed)
        self.overlay.resume_changed.connect(self._on_resume_changed)
        self.overlay.font_size_changed.connect(self._on_font_size_changed)

        # Mic listener
        self.mic_listener = AudioListener(on_text_callback=self._on_mic_text)
        self.signals.audio_text.connect(self._handle_audio_text)

        # System audio listener
        self.system_listener = SystemAudioListener(
            on_text_callback=self._on_sys_text,
            device_index=self._sys_dev_index
        )

        # Auto-scan
        self.auto_scan_active = False
        self.auto_scan_timer  = QTimer()
        self.auto_scan_timer.timeout.connect(self._auto_scan)

        self._register_hotkeys()
        self._build_tray()

    # ── NEW: language / resume / font callbacks ───────────────────────────────

    def _on_language_changed(self, language: str):
        self.current_language = language
        cfg = load_config(); cfg["language"] = language; save_config(cfg)

    def _on_resume_changed(self, resume_text: str):
        self.resume_text = resume_text
        cfg = load_config(); cfg["resume_text"] = resume_text; save_config(cfg)
        word_count = len(resume_text.split()) if resume_text.strip() else 0
        self.tray.showMessage(
            "Resume Saved",
            f"Resume saved ({word_count} words). AI will now use it for experience questions.",
            QSystemTrayIcon.Information, 2500
        )

    def _on_font_size_changed(self, size: int):
        cfg = load_config(); cfg["font_size"] = size; save_config(cfg)

    # ── Tray ─────────────────────────────────────────────────────────────────

    def _make_tray_icon(self):
        size = 64
        pix  = QPixmap(size, size); pix.fill(Qt.transparent)
        p    = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#cba6f7")); p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, size-8, size-8)
        p.setPen(QColor("#1e1e2e"))
        p.setFont(QFont("Segoe UI", 28, QFont.Bold))
        p.drawText(pix.rect(), Qt.AlignCenter, "A")
        p.end()
        return QIcon(pix)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self._make_tray_icon(), self.app)
        S = """
            QMenu {
                background:#1e1e2e; color:#cdd6f4;
                border:1px solid #45475a; border-radius:8px;
                padding:4px 0; font-family:'Segoe UI'; font-size:13px;
            }
            QMenu::item { padding:8px 24px; }
            QMenu::item:selected { background:#313244; }
            QMenu::separator { height:1px; background:#313244; margin:4px 0; }
        """
        menu = QMenu(); menu.setStyleSheet(S)

        # Mode
        mode_menu  = QMenu("🎭 Mode", menu); mode_menu.setStyleSheet(S)
        mode_group = QActionGroup(mode_menu)
        for m in ("interview", "exam", "meeting"):
            act = QAction(m.title(), mode_menu, checkable=True)
            act.setChecked(m == self.current_mode)
            act.triggered.connect(lambda checked, mode=m: self._switch_mode(mode))
            mode_group.addAction(act); mode_menu.addAction(act)
        menu.addMenu(mode_menu); menu.addSeparator()

        scan_act = QAction("🔍 Scan Screen  [Ctrl+Enter]", menu)
        scan_act.triggered.connect(self.scan_screen_action); menu.addAction(scan_act)

        self.mic_action = QAction("🎤 Listen Microphone  [Ctrl+Shift+A]", menu)
        self.mic_action.triggered.connect(self.toggle_mic); menu.addAction(self.mic_action)

        self.sys_action = QAction("🔊 Listen Interviewer Audio  [Ctrl+Shift+S]", menu)
        self.sys_action.triggered.connect(self.toggle_system_audio)
        if not SystemAudioListener.is_available():
            self.sys_action.setText("🔊 System Audio  (pip install soundcard)")
        menu.addAction(self.sys_action)

        pick_act = QAction("🎛️  Select Audio Device (fix if auto-detect fails)", menu)
        pick_act.triggered.connect(self._show_device_picker)
        menu.addAction(pick_act)

        self.auto_scan_action = QAction("🔄 Start Auto-Scan  [Ctrl+Shift+D]", menu)
        self.auto_scan_action.triggered.connect(self.toggle_auto_scan)
        menu.addAction(self.auto_scan_action)

        menu.addSeparator()
        clear_ov = QAction("🗑️ Clear Overlay", menu)
        clear_ov.triggered.connect(self.overlay.clear); menu.addAction(clear_ov)
        menu.addSeparator()

        api_act = QAction("⚙️ Change API Key", menu)
        api_act.triggered.connect(self._prompt_api_key); menu.addAction(api_act)
        menu.addSeparator()

        exit_act = QAction("❌ Exit", menu)
        exit_act.triggered.connect(self._exit); menu.addAction(exit_act)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Interview Assistant — Hidden from screen share")
        self.tray.show()
        self.tray.showMessage(
            "Interview Assistant Ready",
            "Ctrl+Enter=Scan | Ctrl+Shift+A=Mic | Ctrl+Shift+S=Interviewer\n"
            "Use the overlay toolbar to set language and paste your resume.",
            QSystemTrayIcon.Information, 4000
        )

    # ── Hotkeys ───────────────────────────────────────────────────────────────

    def _register_hotkeys(self):
        c = self.config
        try:
            keyboard.add_hotkey(c.get("hotkey_scan_screen",      "ctrl+enter"),   self.scan_screen_action)
            keyboard.add_hotkey(c.get("hotkey_toggle_audio",     "ctrl+shift+a"), self.toggle_mic)
            keyboard.add_hotkey(c.get("hotkey_toggle_auto_scan", "ctrl+shift+d"), self.toggle_auto_scan)
            keyboard.add_hotkey("ctrl+shift+s",                                    self.toggle_system_audio)
            keyboard.add_hotkey(c.get("hotkey_move_up",    "ctrl+up"),    lambda: self.signals.move_window.emit(0,  -20))
            keyboard.add_hotkey(c.get("hotkey_move_down",  "ctrl+down"),  lambda: self.signals.move_window.emit(0,   20))
            keyboard.add_hotkey(c.get("hotkey_move_left",  "ctrl+left"),  lambda: self.signals.move_window.emit(-20,  0))
            keyboard.add_hotkey(c.get("hotkey_move_right", "ctrl+right"), lambda: self.signals.move_window.emit(20,   0))
            keyboard.add_hotkey(c.get("hotkey_resize_up",    "ctrl+shift+up"),    lambda: self.signals.resize_window.emit(0,  -30))
            keyboard.add_hotkey(c.get("hotkey_resize_down",  "ctrl+shift+down"),  lambda: self.signals.resize_window.emit(0,   30))
            keyboard.add_hotkey(c.get("hotkey_resize_left",  "ctrl+shift+left"),  lambda: self.signals.resize_window.emit(-40,  0))
            keyboard.add_hotkey(c.get("hotkey_resize_right", "ctrl+shift+right"), lambda: self.signals.resize_window.emit(40,   0))
        except Exception as e:
            logging.warning("Hotkey registration: %s", e)

    # ── Mode ──────────────────────────────────────────────────────────────────

    def _switch_mode(self, mode):
        self.current_mode = mode
        set_mode(mode); self.overlay.set_mode(mode)
        self.tray.showMessage("Mode Switched", f"Now in {mode.title()} mode",
                              QSystemTrayIcon.Information, 1500)

    # ── API Key ───────────────────────────────────────────────────────────────

    def _prompt_api_key(self):
        dialog = ApiKeyDialog(existing_key=get_api_key())
        if dialog.exec_() == QDialog.Accepted:
            self.config = load_config()
            self.tray.showMessage("API Key Saved", "Groq API key updated.",
                                  QSystemTrayIcon.Information, 2000)

    # ── Device picker ─────────────────────────────────────────────────────────

    def _show_device_picker(self):
        if not SystemAudioListener.is_available():
            self.signals.show_status.emit(
                "⚠️ soundcard not installed.\nRun: pip install soundcard numpy pywin32"
            ); return
        devices = SystemAudioListener.list_system_devices()
        if not devices:
            self.signals.show_status.emit("⚠️ No audio input devices found on this PC."); return
        dialog = DevicePickerDialog(devices)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_index is not None:
            idx = dialog.selected_index
            self._sys_dev_index = idx
            self.config["system_audio_device_index"] = idx; save_config(self.config)
            was_running = self.system_listener.is_listening
            self.system_listener.stop()
            time_sleep_import()
            self.system_listener = SystemAudioListener(
                on_text_callback=self._on_sys_text, device_index=idx
            )
            if was_running: self.system_listener.start()
            dev_name = next((n for i, n, *_ in devices if i == idx), f"Device {idx}")
            self.signals.show_status.emit(f"🔊 Audio device set to:\n{dev_name}")

    # ── Screen Scan ───────────────────────────────────────────────────────────

    def scan_screen_action(self):
        self.signals.show_status.emit("🔍 Scanning screen...")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        try:
            text = scan_screen()
            if not text or text.startswith("[ERROR]"):
                self.signals.show_status.emit(f"⚠️ {text or 'No text detected.'}"); return
            results = process_text(
                text,
                mode=self.current_mode,
                language=self.current_language,
                resume=self.resume_text,
            )
            if results:
                for q, a in results:
                    self.signals.show_answer.emit(q, a)
            else:
                self.signals.show_status.emit("ℹ️ No questions detected on screen.")
        except Exception as e:
            self.signals.show_status.emit(f"❌ Scan error: {str(e)}")
            logging.error("Scan error", exc_info=True)

    # ── Auto-scan ─────────────────────────────────────────────────────────────

    def toggle_auto_scan(self):
        if self.auto_scan_active:
            self.auto_scan_timer.stop(); self.auto_scan_active = False
            self.auto_scan_action.setText("🔄 Start Auto-Scan  [Ctrl+Shift+D]")
            self.signals.show_status.emit("🔄 Auto-scan stopped.")
        else:
            interval = self.config.get("auto_scan_interval_seconds", 10) * 1000
            self.auto_scan_timer.start(interval); self.auto_scan_active = True
            self.auto_scan_action.setText("🔄 Stop Auto-Scan  [Ctrl+Shift+D]")
            self.signals.show_status.emit(
                f"🔄 Auto-scanning every {self.config.get('auto_scan_interval_seconds', 10)}s"
            )

    def _auto_scan(self):
        self.scan_screen_action()

    # ── Mic ───────────────────────────────────────────────────────────────────

    def toggle_mic(self):
        if self.mic_listener.is_listening:
            self.mic_listener.stop()
            self.mic_action.setText("🎤 Listen Microphone  [Ctrl+Shift+A]")
            self.signals.show_status.emit("🎤 Microphone stopped.")
        else:
            self.mic_listener.start()
            self.mic_action.setText("🎤 Stop Microphone  [Ctrl+Shift+A]")
            self.signals.show_status.emit("🎤 Microphone active — listening...")

    def toggle_audio(self):     # backward compat alias
        self.toggle_mic()

    def _on_mic_text(self, text):
        self.signals.audio_text.emit(text)

    def _handle_audio_text(self, text):
        if text.startswith("[AUDIO ERROR]"):
            self.signals.show_status.emit(text); return
        if text.startswith("🔊"):
            self.signals.show_status.emit(text); return
        if is_question(text):
            preview = text[:70] + ("..." if len(text) > 70 else "")
            self.signals.show_status.emit(f"🎤 Heard: \"{preview}\" — Answering...")
            threading.Thread(target=self._solve_text, args=(text,), daemon=True).start()

    # ── System audio ─────────────────────────────────────────────────────────

    def toggle_system_audio(self):
        if not SystemAudioListener.is_available():
            self.signals.show_status.emit(
                "⚠️ soundcard not installed.\nRun: pip install soundcard numpy pywin32"
            ); return
        if self.system_listener.is_listening:
            self.system_listener.stop()
            self.sys_action.setText("🔊 Listen Interviewer Audio  [Ctrl+Shift+S]")
            self.signals.show_status.emit("🔊 System audio stopped.")
        else:
            self.system_listener.start()
            self.sys_action.setText("🔊 Stop Interviewer Audio  [Ctrl+Shift+S]")
            self.signals.show_status.emit(
                "🔊 System audio starting — listening for interviewer speech..."
            )

    def _on_sys_text(self, text):
        self.signals.audio_text.emit(text)

    # ── Manual text input ─────────────────────────────────────────────────────

    def _handle_manual_question(self, text):
        preview = text[:70] + ("..." if len(text) > 70 else "")
        self.signals.show_status.emit(f"✏️ \"{preview}\" — Answering...")
        threading.Thread(target=self._solve_text, args=(text,), daemon=True).start()

    # ── Shared solver — streaming path ───────────────────────────────────────

    def _solve_text(self, question):
        """
        Runs in a worker thread. Streams tokens to the overlay in real time
        so the user sees the answer being written as it is generated.
        """
        self.signals.stream_start.emit(question)
        try:
            solve_streaming(
                question,
                mode=self.current_mode,
                language=self.current_language,
                resume=self.resume_text,
                on_token=lambda tok: self.signals.stream_token.emit(tok),
                on_done=lambda full: self.signals.stream_done.emit(question, full),
                on_error=lambda msg: self.signals.show_status.emit(msg),
            )
        except Exception as e:
            self.signals.show_status.emit(f"❌ {str(e)}")
            logging.error("Solver error", exc_info=True)

    # ── Exit ──────────────────────────────────────────────────────────────────

    def _exit(self):
        for stop in (self.mic_listener.stop, self.system_listener.stop, self.auto_scan_timer.stop):
            try: stop()
            except Exception: pass
        try: keyboard.unhook_all()
        except Exception: pass
        try: self.overlay.close()
        except Exception: pass
        try: self.tray.hide()
        except Exception: pass
        self.app.quit()

    def run(self):
        self.overlay.show()
        return self.app.exec_()


# ── tiny helper ───────────────────────────────────────────────────────────────
def time_sleep_import(secs=0.3):
    import time; time.sleep(secs)


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    _setup_logging()
    sys.excepthook = _global_exception_handler
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        existing_key = get_api_key()
        if not existing_key.strip():
            dialog = ApiKeyDialog(existing_key="")
            if dialog.exec_() != QDialog.Accepted:
                sys.exit(0)
        assistant = MeetingAssistant(app)
        sys.exit(assistant.run())
    except Exception:
        logging.error("Fatal startup error", exc_info=True)
        raise


if __name__ == "__main__":
    main()