"""
Invisible Overlay Window with:
- Font size controls (A− / A+, 8–22 px, persisted)
- Language dropdown (Python, Java, JS … 17 options)
- Resume dialog (paste resume → AI gives personalised answers)

PERF FIXES:
  - Streaming display: stream_start / stream_append / stream_finish
    The answer box begins showing tokens as they arrive instead of waiting
    for the full response.  Existing show_answer() path unchanged (used by
    screen scan).
"""

import ctypes
import re
from collections import deque

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QApplication, QPushButton, QSizeGrip,
    QFrame, QTextEdit, QLineEdit, QComboBox, QDialog,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor

WDA_EXCLUDEFROMCAPTURE = 0x00000011
user32 = ctypes.windll.user32


def hide_from_capture(hwnd: int) -> bool:
    return user32.SetWindowDisplayAffinity(int(hwnd), WDA_EXCLUDEFROMCAPTURE) != 0


def looks_like_code(text: str) -> bool:
    patterns = [
        r'```', r'def \w+\(', r'function \w+\(', r'public \w+ \w+\(',
        r'class \w+[:\{]', r'import \w+', r'#include',
        r'for .+ in .+:', r'if .+:\s*\n', r'\w+\s*=\s*\[', r'return \w+',
    ]
    return any(re.search(p, text) for p in patterns)


MODE_COLORS = {"interview": "#a6e3a1", "exam": "#f9e2af", "meeting": "#89b4fa"}
MODE_LABELS = {"interview": "🎯 Interview", "exam": "📚 Exam", "meeting": "💼 Meeting"}

LANGUAGES = [
    "Auto-Detect", "AI Engineer", "ML Engineer",
    "Python", "Java", "JavaScript", "TypeScript",
    "C++", "C#", "Go", "Rust", "SQL", "React / Next.js",
    "Node.js / Express", "Ruby", "Swift", "Kotlin", "PHP", "Scala",
]
LANGUAGE_ICONS = {
    "Auto-Detect": "🔍", "AI Engineer": "🤖", "ML Engineer": "🧠",
    "Python": "🐍", "Java": "☕", "JavaScript": "🟨",
    "TypeScript": "🔷", "C++": "⚙️", "C#": "💜", "Go": "🐹",
    "Rust": "🦀", "SQL": "🗃️", "React / Next.js": "⚛️",
    "Node.js / Express": "🟩", "Ruby": "💎", "Swift": "🍎",
    "Kotlin": "🎯", "PHP": "🐘", "Scala": "🔴",
}


# ---------------------------------------------------------------------------
# Resume Dialog
# ---------------------------------------------------------------------------

class ResumeDialog(QDialog):
    def __init__(self, current_resume: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("📄 Your Resume — Interview Assistant")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._build_ui(current_resume)

    def _build_ui(self, current_resume: str):
        self.setStyleSheet("""
            QDialog  { background-color: #1e1e2e; }
            QLabel   { color: #cdd6f4; font-family: 'Segoe UI'; background: transparent; }
            QLabel#title { color: #cba6f7; font-size: 16px; font-weight: bold; }
            QLabel#sub   { color: #a6adc8; font-size: 11px; }
            QTextEdit {
                background: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 8px;
                padding: 10px; font-size: 12px; font-family: 'Segoe UI';
            }
            QTextEdit:focus { border: 1px solid #cba6f7; }
            QPushButton#save {
                background: #cba6f7; color: #1e1e2e; border: none;
                border-radius: 8px; padding: 10px 24px;
                font-size: 13px; font-weight: bold; font-family: 'Segoe UI';
            }
            QPushButton#save:hover { background: #d4b8ff; }
            QPushButton#clr {
                background: transparent; color: #f38ba8;
                border: 1px solid #f38ba8; border-radius: 8px;
                padding: 10px 16px; font-size: 12px; font-family: 'Segoe UI';
            }
            QPushButton#clr:hover { background: rgba(243,139,168,20); }
            QPushButton#cancel {
                background: transparent; color: #6c7086;
                border: 1px solid #45475a; border-radius: 8px;
                padding: 10px 20px; font-size: 13px; font-family: 'Segoe UI';
            }
            QPushButton#cancel:hover { color: #a6adc8; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        title = QLabel("📄  Your Resume"); title.setObjectName("title"); lay.addWidget(title)

        sub = QLabel(
            "Paste your full resume below. The AI will use it to give personalised, "
            "first-person answers about your experience, projects, and skills."
        )
        sub.setObjectName("sub"); sub.setWordWrap(True); lay.addWidget(sub)

        self.resume_edit = QTextEdit()
        self.resume_edit.setPlaceholderText(
            "Paste your resume here…\n\n"
            "EXPERIENCE\n"
            "Senior Engineer @ Acme (2021-Present)\n"
            "  - Built microservices with Python / FastAPI\n\n"
            "PROJECTS\n"
            "  - E-commerce: React + Node.js + PostgreSQL\n"
            "  - ML pipeline for churn prediction (scikit-learn, AWS)\n\n"
            "SKILLS\n"
            "  Python, Java, SQL, Docker, Kubernetes, AWS…"
        )
        self.resume_edit.setText(current_resume)
        lay.addWidget(self.resume_edit)

        self.word_count_lbl = QLabel("0 words"); self.word_count_lbl.setObjectName("sub")
        self.resume_edit.textChanged.connect(self._refresh_word_count)
        self._refresh_word_count()
        lay.addWidget(self.word_count_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        clr = QPushButton("🗑️ Clear"); clr.setObjectName("clr")
        clr.setCursor(Qt.PointingHandCursor); clr.clicked.connect(self.resume_edit.clear)
        btn_row.addWidget(clr); btn_row.addStretch()

        cancel = QPushButton("Cancel"); cancel.setObjectName("cancel")
        cancel.setCursor(Qt.PointingHandCursor); cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("✅  Save Resume"); save.setObjectName("save")
        save.setCursor(Qt.PointingHandCursor); save.clicked.connect(self.accept)
        btn_row.addWidget(save)
        lay.addLayout(btn_row)

    def _refresh_word_count(self):
        txt = self.resume_edit.toPlainText().strip()
        self.word_count_lbl.setText(f"{len(txt.split()) if txt else 0} words")

    def get_resume(self) -> str:
        return self.resume_edit.toPlainText().strip()


# ---------------------------------------------------------------------------
# Overlay Window
# ---------------------------------------------------------------------------

class OverlayWindow(QWidget):
    update_content_signal  = pyqtSignal(str, str)
    status_signal          = pyqtSignal(str)
    clear_signal           = pyqtSignal()
    close_app_signal       = pyqtSignal()
    mode_signal            = pyqtSignal(str)
    manual_question_signal = pyqtSignal(str)
    language_changed       = pyqtSignal(str)
    resume_changed         = pyqtSignal(str)
    font_size_changed      = pyqtSignal(int)
    change_api_key_signal  = pyqtSignal()

    # ── NEW: streaming signals (emitted from background thread via main.py) ──
    # These are connected to slots that run on the GUI thread so the text box
    # updates safely even though solve_streaming() runs in a worker thread.
    _stream_start_signal  = pyqtSignal(str)       # question text
    _stream_append_signal = pyqtSignal(str)        # one token chunk
    _stream_finish_signal = pyqtSignal(str, str)   # question, full_answer

    MAX_HISTORY = 8
    FONT_MIN    = 8
    FONT_MAX    = 22

    def __init__(
        self,
        display_seconds:   int = 30,
        initial_font_size: int = 12,
        initial_language:  str = "Auto-Detect",
        initial_resume:    str = "",
    ):
        super().__init__()
        self.display_seconds         = display_seconds
        self._drag_pos               = None
        self._is_hidden_from_capture = False
        self._history                = deque(maxlen=self.MAX_HISTORY)
        self._current_mode           = "interview"
        self._font_size              = max(self.FONT_MIN, min(self.FONT_MAX, initial_font_size))
        self._current_language       = initial_language
        self._resume_text            = initial_resume
        self._history_index          = -1
        self._streaming              = False   # True while tokens are arriving

        self._build_ui()
        self.update_content_signal.connect(self._on_update_content)
        self.status_signal.connect(self._on_status)
        self.clear_signal.connect(self._on_clear)
        self.mode_signal.connect(self._on_mode_changed)

        # Wire streaming signals to their GUI-thread slots
        self._stream_start_signal.connect(self._on_stream_start)
        self._stream_append_signal.connect(self._on_stream_append)
        self._stream_finish_signal.connect(self._on_stream_finish)

    # ------------------------------------------------------------------
    # Build UI  (unchanged from original)
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(420)
        self.setMinimumHeight(240)
        self.resize(580, 460)
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 600, screen.height() - 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self.container = QWidget()
        self.container.setObjectName("Container")
        self.container.setStyleSheet("""
            QWidget#Container {
                background-color: rgba(30, 30, 46, 240);
                border: 1px solid rgba(108, 112, 134, 180);
                border-radius: 14px;
            }
        """)
        root.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(6)

        # Row 1 – header
        header = QHBoxLayout(); header.setSpacing(8)
        self.title_label = QLabel("🎯 Interview Assistant")
        self.title_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.title_label.setStyleSheet("color: #cdd6f4; background: transparent;")
        header.addWidget(self.title_label)

        self.mode_badge = QLabel("🎯 Interview")
        self.mode_badge.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.mode_badge.setStyleSheet(
            f"color:#1e1e2e; background:{MODE_COLORS['interview']}; border-radius:8px; padding:2px 8px;"
        )
        header.addWidget(self.mode_badge)
        header.addStretch()

        _nav = """
            QPushButton { background:rgba(108,112,134,120); color:#cdd6f4; border:none; border-radius:4px; }
            QPushButton:hover { background:rgba(108,112,134,200); }
            QPushButton:disabled { color:rgba(205,214,244,60); background:transparent; }
        """
        self.prev_btn = QPushButton("◀"); self.prev_btn.setFixedSize(22, 22)
        self.next_btn = QPushButton("▶"); self.next_btn.setFixedSize(22, 22)
        for b in (self.prev_btn, self.next_btn):
            b.setFont(QFont("Segoe UI", 8)); b.setStyleSheet(_nav)
        self.prev_btn.setToolTip("Previous answer"); self.next_btn.setToolTip("Next answer")
        self.prev_btn.clicked.connect(self._show_prev)
        self.next_btn.clicked.connect(self._show_next)
        header.addWidget(self.prev_btn); header.addWidget(self.next_btn)

        self.history_label = QLabel("")
        self.history_label.setFont(QFont("Segoe UI", 8))
        self.history_label.setFixedWidth(36)
        self.history_label.setStyleSheet("color:rgba(205,214,244,150); background:transparent;")
        header.addWidget(self.history_label)

        close_btn = QPushButton("✕"); close_btn.setFixedSize(22, 22)
        close_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        close_btn.setStyleSheet("""
            QPushButton { background:transparent; color:rgba(205,214,244,160); border:none; }
            QPushButton:hover { color:#f38ba8; }
        """)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close_app_signal.emit)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Row 2 – toolbar
        toolbar = QHBoxLayout(); toolbar.setSpacing(6)

        a_lbl = QLabel("A"); a_lbl.setFont(QFont("Segoe UI", 8))
        a_lbl.setStyleSheet("color:rgba(205,214,244,130); background:transparent;")
        toolbar.addWidget(a_lbl)

        _btn = """
            QPushButton {
                background:rgba(69,71,90,180); color:#cdd6f4;
                border:none; border-radius:5px;
                font-size:14px; font-weight:bold; font-family:'Segoe UI';
            }
            QPushButton:hover   { background:rgba(108,112,134,220); }
            QPushButton:pressed { background:rgba(203,166,247,180); }
            QPushButton:disabled { color:rgba(205,214,244,40); background:rgba(49,50,68,100); }
        """
        self.font_down_btn = QPushButton("−"); self.font_down_btn.setFixedSize(24, 22)
        self.font_down_btn.setStyleSheet(_btn)
        self.font_down_btn.setToolTip("Decrease font size")
        self.font_down_btn.setCursor(Qt.PointingHandCursor)
        self.font_down_btn.clicked.connect(self._decrease_font)
        toolbar.addWidget(self.font_down_btn)

        self.font_size_lbl = QLabel(f"{self._font_size}px")
        self.font_size_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.font_size_lbl.setFixedWidth(34); self.font_size_lbl.setAlignment(Qt.AlignCenter)
        self.font_size_lbl.setStyleSheet("color:#cba6f7; background:transparent;")
        toolbar.addWidget(self.font_size_lbl)

        self.font_up_btn = QPushButton("+"); self.font_up_btn.setFixedSize(24, 22)
        self.font_up_btn.setStyleSheet(_btn)
        self.font_up_btn.setToolTip("Increase font size")
        self.font_up_btn.setCursor(Qt.PointingHandCursor)
        self.font_up_btn.clicked.connect(self._increase_font)
        toolbar.addWidget(self.font_up_btn)
        self._refresh_font_btn_state()

        vdiv = QFrame(); vdiv.setFrameShape(QFrame.VLine); vdiv.setFixedWidth(1)
        vdiv.setStyleSheet("background:rgba(108,112,134,80); border:none;")
        toolbar.addWidget(vdiv)

        self.lang_combo = QComboBox()
        for lang in LANGUAGES:
            self.lang_combo.addItem(f"{LANGUAGE_ICONS.get(lang,'')} {lang}", lang)
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == self._current_language:
                self.lang_combo.setCurrentIndex(i); break
        self.lang_combo.setFixedHeight(24)
        self.lang_combo.setToolTip("Coding answers will default to this language")
        self.lang_combo.setStyleSheet("""
            QComboBox {
                background:rgba(49,50,68,200); color:#cdd6f4;
                border:1px solid rgba(108,112,134,150); border-radius:6px;
                padding:0 8px; font-size:11px; font-family:'Segoe UI'; min-width:135px;
            }
            QComboBox:hover { border-color:rgba(203,166,247,180); }
            QComboBox::drop-down { border:none; width:18px; }
            QComboBox::down-arrow {
                border-left:4px solid transparent; border-right:4px solid transparent;
                border-top:5px solid #cba6f7; width:0; height:0;
            }
            QComboBox QAbstractItemView {
                background:#313244; color:#cdd6f4;
                border:1px solid #45475a; border-radius:6px;
                selection-background-color:#45475a;
                font-size:11px; font-family:'Segoe UI'; padding:4px;
            }
        """)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_combo_changed)
        toolbar.addWidget(self.lang_combo)
        toolbar.addStretch()

        self.resume_btn = QPushButton("📄 Resume")
        self.resume_btn.setFixedHeight(24)
        self.resume_btn.setCursor(Qt.PointingHandCursor)
        self.resume_btn.setToolTip("Paste your resume for personalised AI answers")
        self.resume_btn.clicked.connect(self._open_resume_dialog)
        self._refresh_resume_btn()
        toolbar.addWidget(self.resume_btn)

        vdiv2 = QFrame(); vdiv2.setFrameShape(QFrame.VLine); vdiv2.setFixedWidth(1)
        vdiv2.setStyleSheet("background:rgba(108,112,134,80); border:none;")
        toolbar.addWidget(vdiv2)

        self.api_key_btn = QPushButton("🔑 API Key")
        self.api_key_btn.setFixedHeight(24)
        self.api_key_btn.setCursor(Qt.PointingHandCursor)
        self.api_key_btn.setToolTip("Change your Groq API key")
        self.api_key_btn.setStyleSheet("""
            QPushButton {
                background: rgba(137,180,250,60); color: #89b4fa;
                border: 1px solid rgba(137,180,250,120); border-radius: 6px;
                padding: 0 10px; font-size: 11px; font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background: rgba(137,180,250,130); color: #cdd6f4;
                border-color: rgba(137,180,250,220);
            }
            QPushButton:pressed { background: rgba(137,180,250,180); }
        """)
        self.api_key_btn.clicked.connect(self.change_api_key_signal.emit)
        toolbar.addWidget(self.api_key_btn)

        layout.addLayout(toolbar)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background:rgba(108,112,134,100); border:none; max-height:1px;")
        layout.addWidget(sep)

        self.question_label = QLabel("")
        self.question_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.question_label.setStyleSheet("color:#a6adc8; background:transparent; padding:2px 0;")
        self.question_label.setWordWrap(True); self.question_label.hide()
        layout.addWidget(self.question_label)

        self.answer_box = QTextEdit()
        self.answer_box.setReadOnly(True)
        self.answer_box.setFrameShape(QFrame.NoFrame)
        self.answer_box.setFont(QFont("Segoe UI", self._font_size))
        self.answer_box.setStyleSheet("""
            QTextEdit {
                background:transparent; color:#cdd6f4;
                font-family:'Segoe UI'; border:none; padding:0;
            }
            QScrollBar:vertical { background:rgba(49,50,68,180); width:6px; border-radius:3px; }
            QScrollBar::handle:vertical {
                background:rgba(108,112,134,180); border-radius:3px; min-height:20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)
        layout.addWidget(self.answer_box)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("background:rgba(108,112,134,80); border:none; max-height:1px;")
        layout.addWidget(sep2)

        input_row = QHBoxLayout(); input_row.setSpacing(6)
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("✏️  Type a question and press Enter…")
        self.text_input.setStyleSheet("""
            QLineEdit {
                background:rgba(49,50,68,200); color:#cdd6f4;
                border:1px solid rgba(108,112,134,150); border-radius:8px;
                padding:6px 10px; font-size:12px; font-family:'Segoe UI';
            }
            QLineEdit:focus { border:1px solid rgba(203,166,247,200); }
        """)
        self.text_input.returnPressed.connect(self._on_manual_submit)
        input_row.addWidget(self.text_input)
        ask_btn = QPushButton("Ask"); ask_btn.setFixedSize(48, 32)
        ask_btn.setCursor(Qt.PointingHandCursor)
        ask_btn.setStyleSheet("""
            QPushButton {
                background:rgba(203,166,247,200); color:#1e1e2e;
                border:none; border-radius:8px;
                font-size:12px; font-weight:bold; font-family:'Segoe UI';
            }
            QPushButton:hover   { background:rgba(212,184,255,220); }
            QPushButton:pressed { background:rgba(180,140,240,220); }
        """)
        ask_btn.clicked.connect(self._on_manual_submit)
        input_row.addWidget(ask_btn)
        layout.addLayout(input_row)

        bottom = QHBoxLayout(); bottom.setSpacing(4)
        self.status_label = QLabel("🔒 Hidden from screen capture")
        self.status_label.setFont(QFont("Segoe UI", 8))
        self.status_label.setStyleSheet("color:rgba(166,227,161,200); background:transparent;")
        bottom.addWidget(self.status_label); bottom.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("background:transparent;")
        bottom.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # Font size
    # ------------------------------------------------------------------

    def _increase_font(self):
        if self._font_size < self.FONT_MAX:
            self._font_size += 1; self._apply_font()
            self.font_size_changed.emit(self._font_size)

    def _decrease_font(self):
        if self._font_size > self.FONT_MIN:
            self._font_size -= 1; self._apply_font()
            self.font_size_changed.emit(self._font_size)

    def _apply_font(self):
        self.font_size_lbl.setText(f"{self._font_size}px")
        self.answer_box.setFont(QFont("Segoe UI", self._font_size))
        self._refresh_font_btn_state()

    def _refresh_font_btn_state(self):
        self.font_down_btn.setEnabled(self._font_size > self.FONT_MIN)
        self.font_up_btn.setEnabled(self._font_size < self.FONT_MAX)

    @property
    def font_size(self) -> int:
        return self._font_size

    # ------------------------------------------------------------------
    # Language dropdown
    # ------------------------------------------------------------------

    def _on_lang_combo_changed(self, index: int):
        lang = self.lang_combo.itemData(index)
        if lang:
            self._current_language = lang
            self.language_changed.emit(lang)

    def set_language(self, language: str):
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == language:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentIndex(i)
                self.lang_combo.blockSignals(False)
                self._current_language = language
                break

    # ------------------------------------------------------------------
    # Resume dialog
    # ------------------------------------------------------------------

    def _open_resume_dialog(self):
        dlg = ResumeDialog(current_resume=self._resume_text, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self._resume_text = dlg.get_resume()
            self.resume_changed.emit(self._resume_text)
            self._refresh_resume_btn()

    def _refresh_resume_btn(self):
        if self._resume_text.strip():
            self.resume_btn.setText("📄 Resume ✅")
            self.resume_btn.setStyleSheet("""
                QPushButton {
                    background:rgba(166,227,161,40); color:#a6e3a1;
                    border:1px solid rgba(166,227,161,130); border-radius:6px;
                    padding:0 10px; font-size:11px; font-family:'Segoe UI';
                }
                QPushButton:hover { background:rgba(166,227,161,80); }
            """)
        else:
            self.resume_btn.setText("📄 Resume")
            self.resume_btn.setStyleSheet("""
                QPushButton {
                    background:rgba(137,180,250,40); color:#89b4fa;
                    border:1px solid rgba(137,180,250,120); border-radius:6px;
                    padding:0 10px; font-size:11px; font-family:'Segoe UI';
                }
                QPushButton:hover { background:rgba(137,180,250,80); border-color:#89b4fa; }
            """)

    def set_resume(self, resume_text: str):
        self._resume_text = resume_text; self._refresh_resume_btn()

    # ------------------------------------------------------------------
    # Manual submit
    # ------------------------------------------------------------------

    def _on_manual_submit(self):
        text = self.text_input.text().strip()
        if text:
            self.text_input.clear(); self.manual_question_signal.emit(text)

    # ------------------------------------------------------------------
    # Show event
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._is_hidden_from_capture:
            ok = hide_from_capture(int(self.winId()))
            self._is_hidden_from_capture = ok
            if ok:
                self.status_label.setText("🔒 Hidden from screen capture")
                self.status_label.setStyleSheet("color:rgba(166,227,161,200); background:transparent;")
            else:
                self.status_label.setText("⚠️ Could not hide (Win10 v2004+ required)")
                self.status_label.setStyleSheet("color:rgba(243,139,168,200); background:transparent;")

    # ------------------------------------------------------------------
    # Streaming slots  (NEW)
    # ------------------------------------------------------------------

    def _on_stream_start(self, question: str):
        """Called when a streaming answer begins — clears the box and shows the question."""
        self._streaming = True
        if question:
            self.question_label.setText(f"❓ {question[:250]}")
            self.question_label.show()
        else:
            self.question_label.hide()
        self.answer_box.setFont(QFont("Segoe UI", self._font_size))
        self.answer_box.setPlainText("")
        self.show(); self.raise_()

    def _on_stream_append(self, token: str):
        """Appends a token chunk to the answer box in real time."""
        if not self._streaming:
            return
        cursor = self.answer_box.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.answer_box.setTextCursor(cursor)
        self.answer_box.insertPlainText(token)
        # Auto-scroll to keep the latest text visible
        self.answer_box.verticalScrollBar().setValue(
            self.answer_box.verticalScrollBar().maximum()
        )

    def _on_stream_finish(self, question: str, full_answer: str):
        """
        Called once streaming is complete.  Adds to history and re-renders
        with the correct font (code font if the answer contains code).
        """
        self._streaming = False
        if question and full_answer:
            self._history.append((question, full_answer))
        self._history_index = -1

        # If answer contains code, switch font and re-render cleanly
        if looks_like_code(full_answer):
            self.answer_box.setFont(QFont("Consolas", max(self._font_size - 1, self.FONT_MIN)))
            self.answer_box.setPlainText(full_answer)
            cur = self.answer_box.textCursor(); cur.movePosition(QTextCursor.Start)
            self.answer_box.setTextCursor(cur)

        total = len(self._history)
        if total:
            self.history_label.setText(f"{total}/{total}")
        self._update_nav_buttons()

    # ------------------------------------------------------------------
    # Existing content update slots  (unchanged — used by screen scan)
    # ------------------------------------------------------------------

    def _on_update_content(self, question: str, answer: str):
        self._streaming = False   # cancel any in-progress stream if screen-scan fires
        if question or answer:
            if question and answer:
                self._history.append((question, answer))
            self._history_index = -1
            self._render_entry(question, answer)
        self.show(); self.raise_()

    def _render_entry(self, question: str, answer: str):
        if question:
            self.question_label.setText(f"❓ {question[:250]}"); self.question_label.show()
        else:
            self.question_label.hide()
        if looks_like_code(answer):
            self.answer_box.setFont(QFont("Consolas", max(self._font_size - 1, self.FONT_MIN)))
        else:
            self.answer_box.setFont(QFont("Segoe UI", self._font_size))
        self.answer_box.setPlainText(answer)
        cur = self.answer_box.textCursor(); cur.movePosition(QTextCursor.Start)
        self.answer_box.setTextCursor(cur)
        total = len(self._history)
        if total:
            idx = total if self._history_index == -1 else self._history_index + 1
            self.history_label.setText(f"{idx}/{total}")
        else:
            self.history_label.setText("")
        self._update_nav_buttons()

    def _on_status(self, message: str):
        self._streaming = False
        self.question_label.hide()
        self.answer_box.setFont(QFont("Segoe UI", self._font_size))
        self.answer_box.setPlainText(message)
        self.show(); self.raise_()

    def _on_clear(self):
        self._streaming = False
        self.question_label.hide()
        self.answer_box.setPlainText("")
        self.answer_box.setPlaceholderText(
            "Ready — Ctrl+Enter scan | Ctrl+Shift+A mic | Ctrl+Shift+S system audio"
        )
        self.history_label.setText(""); self._update_nav_buttons()

    def _on_mode_changed(self, mode: str):
        self._current_mode = mode
        self.mode_badge.setText(MODE_LABELS.get(mode, mode.title()))
        color = MODE_COLORS.get(mode, "#cdd6f4")
        self.mode_badge.setStyleSheet(
            f"color:#1e1e2e; background:{color}; border-radius:8px; padding:2px 8px;"
        )

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def _update_nav_buttons(self):
        total = len(self._history)
        self.prev_btn.setEnabled(total > 1)
        self.next_btn.setEnabled(
            total > 1 and self._history_index != -1 and self._history_index < total - 1
        )

    def _show_prev(self):
        total = len(self._history)
        if not total: return
        if self._history_index == -1: self._history_index = total - 2
        elif self._history_index > 0: self._history_index -= 1
        q, a = self._history[self._history_index]; self._render_entry(q, a)

    def _show_next(self):
        total = len(self._history)
        if not total or self._history_index == -1: return
        if self._history_index < total - 1:
            self._history_index += 1
            if self._history_index == total - 1: self._history_index = -1
        q, a = self._history[-1 if self._history_index == -1 else self._history_index]
        self._render_entry(q, a)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_answer(self, question: str, answer: str):
        """Non-streaming path (used by screen scan)."""
        self.update_content_signal.emit(question, answer)

    def show_status(self, message: str):
        self.status_signal.emit(message)

    def clear(self):
        self.clear_signal.emit()

    def set_mode(self, mode: str):
        self.mode_signal.emit(mode)

    # ── Streaming public API (called from main.py worker signals) ────────────

    def stream_start(self, question: str):
        """Thread-safe: begin a streaming answer."""
        self._stream_start_signal.emit(question)

    def stream_append(self, token: str):
        """Thread-safe: append a token chunk."""
        self._stream_append_signal.emit(token)

    def stream_finish(self, question: str, full_answer: str):
        """Thread-safe: mark streaming complete and finalise history."""
        self._stream_finish_signal.emit(question, full_answer)

    # ------------------------------------------------------------------
    # Window interaction
    # ------------------------------------------------------------------

    def fade_out(self):
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def move_relative(self, dx: int, dy: int):
        self.move(self.x() + dx, self.y() + dy)

    def resize_relative(self, dw: int, dh: int):
        self.resize(
            max(self.minimumWidth(),  self.width()  + dw),
            max(self.minimumHeight(), self.height() + dh),
        )