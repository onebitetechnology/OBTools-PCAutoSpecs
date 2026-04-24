"""
PC AutoSpec — Dialogs (PySide6).
SettingsDialog: API key, URL, test connection.
ReportPreviewDialog: rendered HTML preview matching RepairDesk display.
DetailDialog: drill-down popup for diagnostic sections.
"""

import os
import platform
import re
import logging
import sys
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QDateTime
from PySide6.QtGui import QPalette, QColor, QDesktopServices

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QCheckBox, QWidget, QApplication,
    QScrollArea, QFrame, QProgressBar, QSizePolicy,
    QMessageBox,
    QComboBox,
    QListWidget,
    QListWidgetItem,
)
from theme import COLORS
from settings import (
    APP_NAME, APP_VERSION, load_settings, save_settings, is_configured, DEFAULTS,
    SCAN_CATEGORIES, SCAN_CATEGORY_GROUPS,
    UPLOAD_SCOPE_CHOICES, DEFAULT_UPLOAD_SCOPE,
    UPLOAD_SCOPE_OVERVIEW, UPLOAD_SCOPE_FULL,
    get_tech_names, get_tech_api_key, save_technicians,
    get_technicians, get_app_dir, get_auth_mode,
)
from oauth_repairdesk import clear_oauth_tokens, oauth_is_connected, run_oauth_flow
from repairdesk_api import RepairDeskAPI
from updater import get_pending_update, launch_pending_update
from workers import UpdateCheckWorker, UpdateDownloadWorker

README_FILENAME = "PC AutoSpec Read Me.md"



# ─── QMessageBox light-theme helper ─────────────────────────────────
# The app uses a dark global stylesheet; native QMessageBox inherits it
# and becomes unreadable. This helper overrides the palette to be light.
def _checkmark_svg_path():
    """
    Write a white checkmark SVG to a temp file and return its path.
    Qt stylesheets cannot use data: URIs for images, but CAN use file paths.
    We cache the path so the file is only written once per session.
    """
    import tempfile, os
    if not hasattr(_checkmark_svg_path, '_cached'):
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'>"
            "<polyline points='2,7 5.5,10.5 12,3' "
            "stroke='white' stroke-width='2.2' fill='none' "
            "stroke-linecap='round' stroke-linejoin='round'/>"
            "</svg>"
        )
        tmp = tempfile.NamedTemporaryFile(
            suffix='.svg', delete=False, mode='w', encoding='utf-8')
        tmp.write(svg)
        tmp.close()
        _checkmark_svg_path._cached = tmp.name.replace('\\', '/')
    return _checkmark_svg_path._cached


_MSGBOX_STYLE = """
    QMessageBox {
        background-color: #f5f5f5;
        color: #1a1a1a;
    }
    QMessageBox QLabel {
        color: #1a1a1a;
        font-size: 10pt;
        min-width: 320px;
    }
    QMessageBox QPushButton {
        background-color: #10B981;
        color: white;
        border: none;
        border-radius: 5px;
        padding: 8px 28px;
        font-size: 10pt;
        min-width: 120px;
    }
    QMessageBox QPushButton:hover {
        background-color: #059669;
    }
    QMessageBox QPushButton:focus {
        outline: none;
        border: 2px solid #047857;
    }
"""

def _make_msgbox(parent, title, text, buttons=None, default=None):
    """
    Create a light-themed QMessageBox readable against the dark app.

    KEY: We do NOT pass the dark-themed parent to QMessageBox — doing so
    causes Qt on Windows to inherit the parent's dark stylesheet down to
    the box's internal QLabel children, painting the window dark blue even
    when a light stylesheet is applied.  By passing None we break the
    inheritance chain; we then call setWindowModality to keep it modal and
    center it over the parent manually.
    """
    box = QMessageBox()          # <-- no parent = no style inheritance
    box.setWindowTitle(title)
    box.setText(text)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setWindowModality(Qt.WindowModality.ApplicationModal)
    box.setMinimumWidth(560)

    # Light palette so text is readable
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,      QColor("#f5f5f5"))
    pal.setColor(QPalette.ColorRole.WindowText,  QColor("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.Base,        QColor("#f5f5f5"))
    pal.setColor(QPalette.ColorRole.Text,        QColor("#1a1a1a"))
    pal.setColor(QPalette.ColorRole.ButtonText,  QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Button,      QColor("#10B981"))
    box.setPalette(pal)
    box.setStyleSheet(_MSGBOX_STYLE)

    if buttons is not None:
        box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)

    for button in box.findChildren(QPushButton):
        button.setMinimumWidth(132)
        button.setMinimumHeight(38)

    # Force the main text label to wrap and reserve enough width before the
    # final size calculation, otherwise Windows DPI scaling can clip longer
    # confirmation prompts like the updater install dialog.
    box.ensurePolished()
    for label in box.findChildren(QLabel):
        if label.text() == text:
            label.setWordWrap(True)
            label.setMinimumWidth(520)
            label.setMaximumWidth(720)
            break

    # Force the final wrapped size before centering so text does not clip on
    # Windows DPI scaling, especially for multi-line confirmation prompts.
    box.layout().activate()
    box.adjustSize()
    hint = box.sizeHint()
    box.resize(max(620, hint.width()), max(200, hint.height()))

    # Centre over the real parent window using the final size hint so text
    # and buttons are not clipped on scaled Windows displays.
    if parent is not None:
        geo = parent.geometry()
        box.move(
            geo.x() + max(0, (geo.width() - box.width()) // 2),
            geo.y() + max(0, (geo.height() - box.height()) // 2),
        )
    return box


class RoundedProgressWidget(QWidget):
    """Progress widget with a rounded track and centered text label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self._format = "%p%"
        self._text_visible = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._track = QFrame()
        self._track.setObjectName("updateProgressTrack")
        self._track.setFixedHeight(24)
        self._track.setStyleSheet(f"""
            QFrame#updateProgressTrack {{
                background-color: {COLORS['console_bg']};
                border: 1px solid {COLORS['card_border']};
                border-radius: 12px;
            }}
        """)
        layout.addWidget(self._track)

        stack = QGridLayout(self._track)
        stack.setContentsMargins(2, 2, 2, 2)
        stack.setSpacing(0)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setRange(self._minimum, self._maximum)
        self._bar.setValue(self._value)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background: transparent;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {COLORS['primary']};
                border-radius: 9px;
            }}
        """)
        stack.addWidget(self._bar, 0, 0)

        self._label = QLabel("0%")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 10pt; font-weight: bold; border: none;"
        )
        stack.addWidget(self._label, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)

    def setFixedHeight(self, height):
        super().setFixedHeight(height)
        self._track.setFixedHeight(height)

    def setRange(self, minimum, maximum):
        self._minimum = minimum
        self._maximum = maximum
        self._bar.setRange(minimum, maximum)
        self._update_label()

    def setValue(self, value):
        self._value = value
        self._bar.setValue(value)
        self._update_label()

    def setTextVisible(self, visible):
        self._text_visible = bool(visible)
        self._label.setVisible(self._text_visible)

    def setFormat(self, fmt):
        self._format = fmt or "%p%"
        self._update_label()

    def _update_label(self):
        if not self._text_visible:
            return
        span = max(1, self._maximum - self._minimum)
        percent = int(round(((self._value - self._minimum) / span) * 100))
        percent = max(0, min(100, percent))
        text = self._format.replace("%p%", f"{percent}%")
        self._label.setText(text)


# ─── Startup / Job Setup Dialog ──────────────────────────────────────

class StartupDialog(QDialog):
    """
    Shown at startup before every scan.
    Tech enters: name, ticket ID (with confirmation), report type,
    tech notes, and selects which test categories to run.

    Attributes set on accept or skip:
        tech_name      (str)
        ticket_id      (str)   — raw number, e.g. "15108"
        ticket_info    (dict)  — from API, or {} if not confirmed
        report_type    (str)   — "Initial Device Report" | "Final Device Report (Post Repair)"
        upload_scope   (str)   — "overview" | "full"
        tech_notes     (str)
        skip_categories (set)  — category keys NOT selected
        skip_scan_requested (bool) — explicit request to leave without starting a scan
    """

    REPORT_TYPE_INITIAL = "Initial Device Report"
    REPORT_TYPE_FINAL   = "Final Device Report (Post Repair)"

    def __init__(self, parent=None, prefill_tech_name=""):
        super().__init__(parent)
        self.setWindowTitle("Job Setup")
        self.setMinimumSize(860, 700)

        # Result attributes (populated on accept or left as defaults on skip)
        self.tech_name       = ""
        self.ticket_id       = ""
        self.ticket_info     = {}
        self.report_type     = ""
        self.upload_scope    = DEFAULT_UPLOAD_SCOPE
        self.tech_notes      = ""
        self.skip_categories = set()
        self.skip_scan_requested = False
        self.quick_upload_requested = False

        self.setObjectName("jobSetupDialog")
        self.setStyleSheet(
            f"QDialog#jobSetupDialog {{ background-color: {COLORS['bg_root']}; }}"
        )

        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(
                min(1320, max(860, available.width() - 80)),
                min(1040, max(700, available.height() - 90)),
            )
            self.setMaximumSize(
                max(860, available.width() - 40),
                max(700, available.height() - 40),
            )
        else:
            self.resize(1120, 900)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(54)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)
        title_lbl = QLabel("Job Setup")
        title_lbl.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 13px; font-weight: bold;")
        h_layout.addWidget(title_lbl)
        h_layout.addStretch()
        hint = QLabel(f"v{APP_VERSION}  —  Enter details before starting the scan")
        hint.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 9pt;")
        h_layout.addWidget(hint)
        outer.addWidget(header)

        # ── Scrollable body ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(scroll, 1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(16)
        scroll.setWidget(body)

        # Helper: section label style
        def _section_lbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
            return lbl

        # Helper: input style
        input_style = (
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 6px 10px; font-size: 10pt;"
        )

        # ── Tech Name / Selector ──────────────────────────────────
        s = load_settings()
        tech_names = get_tech_names()
        tech_header_row = QHBoxLayout()
        tech_header_row.setContentsMargins(0, 0, 0, 0)
        tech_header_row.addWidget(_section_lbl("Technician"))
        tech_header_row.addStretch()
        # Manage Techs moved to Settings dialog
        body_layout.addLayout(tech_header_row)

        if tech_names:
            # Dropdown — techs are configured
            self._name_combo = QComboBox()
            self._name_combo.setFixedHeight(36)
            self._name_combo.setStyleSheet(
                f"background-color: {COLORS['console_bg']}; "
                f"color: {COLORS['console_text']}; "
                f"border: 1px solid {COLORS['card_border']}; "
                "border-radius: 6px; padding: 2px 8px; font-size: 10pt; "
                f"selection-background-color: {COLORS['success']};"
            )
            for name in tech_names:
                self._name_combo.addItem(name)
            # Pre-select last used tech
            last = prefill_tech_name or s.get('last_tech_name', '')
            if last in tech_names:
                self._name_combo.setCurrentText(last)
            body_layout.addWidget(self._name_combo)
            self._name_input = None  # not used in dropdown mode
            self._tech_mode = 'combo'
        else:
            # Free text — no techs configured yet
            self._name_input = QLineEdit()
            self._name_input.setPlaceholderText("Your first name  (add techs via ⚙ Settings → Manage Techs)")
            self._name_input.setFixedHeight(36)
            self._name_input.setStyleSheet(input_style)
            self._name_input.setText(prefill_tech_name or s.get('last_tech_name', ''))
            body_layout.addWidget(self._name_input)
            self._name_combo = None
            self._tech_mode = 'text'

        # ── Ticket ID + Confirm ───────────────────────────────────
        body_layout.addWidget(_section_lbl("Ticket ID  (optional — can be entered at upload)"))
        ticket_row = QHBoxLayout()
        ticket_row.setSpacing(8)
        self._ticket_input = QLineEdit()
        self._ticket_input.setPlaceholderText("e.g. 15108  or  T-15108")
        self._ticket_input.setFixedHeight(36)
        self._ticket_input.setStyleSheet(input_style)
        self._ticket_input.textChanged.connect(self._on_ticket_text_changed)
        ticket_row.addWidget(self._ticket_input, 1)

        self._confirm_btn = QPushButton("Confirm Ticket")
        self._confirm_btn.setFixedHeight(36)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setCursor(Qt.PointingHandCursor)
        self._confirm_btn.setStyleSheet(
            f"background-color: {COLORS['primary']}; color: white; "
            "border: none; border-radius: 6px; font-weight: bold; padding: 0 16px;")
        self._confirm_btn.clicked.connect(self._on_confirm_ticket)
        ticket_row.addWidget(self._confirm_btn)
        body_layout.addLayout(ticket_row)

        # Ticket status label (shows customer/device after confirmation)
        self._ticket_status = QLabel("")
        self._ticket_status.setWordWrap(True)
        self._ticket_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        body_layout.addWidget(self._ticket_status)

        # ── Report Type ───────────────────────────────────────────
        body_layout.addWidget(_section_lbl("Report Type  ✱ Required"))

        # Large toggle-style buttons — much more obvious than radio buttons
        _btn_base = (
            "QPushButton {"
            "  border: 2px solid #374151;"
            "  border-radius: 8px;"
            "  padding: 12px 20px;"
            "  font-size: 11pt;"
            "  font-weight: bold;"
            "  text-align: center;"
            "  color: #9CA3AF;"
            "  background-color: #1F2937;"
            "}"
            "QPushButton:hover {"
            "  border-color: #6B7280;"
            "  color: #D1D5DB;"
            "  background-color: #374151;"
            "}"
        )
        _btn_selected = (
            "QPushButton {"
            "  border: 2px solid #10B981;"
            "  border-radius: 8px;"
            "  padding: 12px 20px;"
            "  font-size: 11pt;"
            "  font-weight: bold;"
            "  text-align: center;"
            "  color: #ffffff;"
            "  background-color: #065F46;"
            "}"
        )

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)

        self._radio_initial = QPushButton("🔍  Initial Device Report")
        self._radio_initial.setCheckable(True)
        self._radio_initial.setFixedHeight(56)
        self._radio_initial.setStyleSheet(_btn_base)

        self._radio_final = QPushButton("✅  Final Device Report (Post Repair)")
        self._radio_final.setCheckable(True)
        self._radio_final.setFixedHeight(56)
        self._radio_final.setStyleSheet(_btn_base)

        # Store styles for toggling
        self._rtype_base = _btn_base
        self._rtype_selected = _btn_selected

        def _select_initial():
            self._radio_initial.setChecked(True)
            self._radio_final.setChecked(False)
            self._radio_initial.setStyleSheet(_btn_selected)
            self._radio_final.setStyleSheet(_btn_base)
            self._report_type_warning.setVisible(False)

        def _select_final():
            self._radio_final.setChecked(True)
            self._radio_initial.setChecked(False)
            self._radio_final.setStyleSheet(_btn_selected)
            self._radio_initial.setStyleSheet(_btn_base)
            self._report_type_warning.setVisible(False)

        self._radio_initial.clicked.connect(_select_initial)
        self._radio_final.clicked.connect(_select_final)

        toggle_row.addWidget(self._radio_initial)
        toggle_row.addWidget(self._radio_final)
        body_layout.addLayout(toggle_row)

        self._report_type_warning = QLabel("⚠  Please select a report type before starting")
        self._report_type_warning.setStyleSheet("color: #EF4444; font-size: 9pt;")
        self._report_type_warning.setVisible(False)
        body_layout.addWidget(self._report_type_warning)

        # ── Upload Scope ────────────────────────────────────────
        body_layout.addWidget(_section_lbl("Upload Content"))

        upload_row = QHBoxLayout()
        upload_row.setSpacing(12)

        self._upload_overview_btn = QPushButton("Upload System Overview only")
        self._upload_overview_btn.setCheckable(True)
        self._upload_overview_btn.setFixedHeight(50)
        self._upload_overview_btn.setStyleSheet(_btn_base)

        self._upload_full_btn = QPushButton("Upload full results")
        self._upload_full_btn.setCheckable(True)
        self._upload_full_btn.setFixedHeight(50)
        self._upload_full_btn.setStyleSheet(_btn_base)

        def _select_upload_scope(scope):
            overview_selected = scope == UPLOAD_SCOPE_OVERVIEW
            self._upload_overview_btn.setChecked(overview_selected)
            self._upload_full_btn.setChecked(not overview_selected)
            self._upload_overview_btn.setStyleSheet(
                _btn_selected if overview_selected else _btn_base)
            self._upload_full_btn.setStyleSheet(
                _btn_selected if not overview_selected else _btn_base)
            self.upload_scope = scope
            self._refresh_start_button()

        self._upload_overview_btn.clicked.connect(
            lambda: _select_upload_scope(UPLOAD_SCOPE_OVERVIEW))
        self._upload_full_btn.clicked.connect(
            lambda: _select_upload_scope(UPLOAD_SCOPE_FULL))
        _select_upload_scope(DEFAULT_UPLOAD_SCOPE)

        upload_row.addWidget(self._upload_overview_btn)
        upload_row.addWidget(self._upload_full_btn)
        body_layout.addLayout(upload_row)

        upload_hint = QLabel(
            "System Overview only uploads OS, CPU, RAM, and drive capacity usage. "
            "Full results uploads the complete diagnostic report."
        )
        upload_hint.setWordWrap(True)
        upload_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        body_layout.addWidget(upload_hint)

        # ── Tech Notes ────────────────────────────────────────────
        body_layout.addWidget(_section_lbl(
            "Tech Notes  (optional — also editable at upload time)"))
        self._notes_input = QTextEdit()
        self._notes_input.setPlaceholderText(
            "e.g. customer reports slow startup, cracked hinge, fan noise...")
        self._notes_input.setFixedHeight(72)
        self._notes_input.setStyleSheet(
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 8px; font-size: 10pt;")
        body_layout.addWidget(self._notes_input)

        # ── Test Categories ───────────────────────────────────────
        body_layout.addWidget(_section_lbl(
            "Tests to Run  (basic system info is always collected)"))

        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(8)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setObjectName("secondary")
        select_all_btn.setCursor(Qt.PointingHandCursor)
        select_all_btn.clicked.connect(lambda: self._set_all_categories(True))
        bulk_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setObjectName("secondary")
        deselect_all_btn.setCursor(Qt.PointingHandCursor)
        deselect_all_btn.clicked.connect(lambda: self._set_all_categories(False))
        bulk_row.addWidget(deselect_all_btn)

        bulk_row.addStretch()
        body_layout.addLayout(bulk_row)

        self._category_checks = {}
        checkbox_style = f"""
            QCheckBox {{
                color: {COLORS['console_text']};
                font-size: 10pt;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {COLORS['success']};
                border-radius: 4px;
                background: transparent;
            }}
            QCheckBox::indicator:unchecked {{
                background-color: transparent;
            }}
            QCheckBox::indicator:checked {{
                background-color: {COLORS['success']};
                border: 2px solid {COLORS['success']};
                image: url({_checkmark_svg_path()});
            }}
        """

        for group_name, categories in SCAN_CATEGORY_GROUPS:
            group_lbl = QLabel(group_name)
            group_lbl.setStyleSheet(
                f"color: {COLORS['text_tertiary']}; font-size: 9pt; font-weight: bold;")
            body_layout.addWidget(group_lbl)

            grid = QGridLayout()
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(10)

            for index, (key, label) in enumerate(categories):
                cb = QCheckBox(label)
                cb.setChecked(True)
                cb.setStyleSheet(checkbox_style)
                self._category_checks[key] = cb
                row = index // 2
                col = index % 2
                grid.addWidget(cb, row, col)

            body_layout.addLayout(grid)

        body_layout.addStretch()

        # ── Bottom action bar ─────────────────────────────────────
        footer = QFrame()
        footer.setObjectName("jobSetupFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setFixedHeight(72)
        footer.setStyleSheet(
            f"QFrame#jobSetupFooter {{ "
            f"background-color: {COLORS['card_bg']}; "
            f"border-top: 1px solid {COLORS['card_border']}; "
            f"}}"
        )

        btn_row = QHBoxLayout(footer)
        btn_row.setContentsMargins(24, 14, 24, 14)
        btn_row.setSpacing(10)

        self._skip_btn = QPushButton("Skip, Don't Scan")
        self._skip_btn.setFixedHeight(42)
        self._skip_btn.setCursor(Qt.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(self._skip_btn)

        btn_row.addStretch()

        self._start_btn = QPushButton("Start Scan  ▶")
        self._start_btn.setFixedHeight(42)
        self._start_btn.setMinimumWidth(320)
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)

        outer.addWidget(footer, 0)
        footer.raise_()
        self._apply_job_setup_button_styles()
        self._refresh_start_button()

    # ── Ticket handling ───────────────────────────────────────────

    def _on_ticket_text_changed(self, text):
        has_text = len(text.strip()) > 0
        self._confirm_btn.setEnabled(has_text)
        # Reset confirmation if text changes after a confirm
        if self.ticket_info:
            self.ticket_info = {}
            self.ticket_id   = ""
            self._ticket_status.setText("")
            self._ticket_status.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 9pt;")

    def _on_confirm_ticket(self):
        """Fetch ticket from RepairDesk and show customer/device confirmation."""
        raw = self._ticket_input.text().strip()
        if not raw:
            return
        ticket_num = raw.upper().lstrip("T-").lstrip("T") if raw.upper().startswith("T") else raw
        # Strip any leading dashes
        ticket_num = ticket_num.lstrip("-")

        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setText("Confirming...")
        self._ticket_status.setText("Looking up ticket...")
        self._ticket_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        QApplication.processEvents()

        try:
            api = RepairDeskAPI()
            info = api.get_ticket_customer(ticket_num)

            customer = info.get('customer_name', 'Unknown')
            device   = info.get('device', '')
            t_num    = info.get('ticket_number', ticket_num)

            # Show inline confirmation
            msg = f"T-{t_num}  ·  {customer}"
            if device:
                msg += f"  ·  {device}"

            # Ask for confirmation via message box (same flow as upload)
            _box = _make_msgbox(
                self, "Confirm Ticket",
                f"Is this the correct ticket?\n\nTicket:    T-{t_num}\nCustomer:  {customer}\n"
                + (f"Device:    {device}\n" if device else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            reply = _box.exec()

            if reply == QMessageBox.StandardButton.Yes.value:
                self.ticket_id   = ticket_num
                self.ticket_info = info
                self._ticket_status.setText(f"✓  {msg}")
                self._ticket_status.setStyleSheet(
                    f"color: {COLORS['success']}; font-size: 9pt; font-weight: bold;")
                self._confirm_btn.setText("✓ Confirmed")
                self._confirm_btn.setEnabled(False)
                self._confirm_btn.setStyleSheet(
                    f"background-color: {COLORS['success']}; color: white; "
                    "border: none; border-radius: 6px; font-weight: bold; padding: 0 16px;")
            else:
                self._ticket_status.setText("Not confirmed — enter a different ticket number")
                self._ticket_status.setStyleSheet(
                    f"color: {COLORS['warning']}; font-size: 9pt;")
                self._confirm_btn.setText("Confirm Ticket")
                self._confirm_btn.setEnabled(True)
                self.ticket_info = {}
                self.ticket_id   = ""
            self._refresh_start_button()

        except Exception as e:
            self._ticket_status.setText(f"⚠ Could not look up ticket: {e}")
            self._ticket_status.setStyleSheet(
                f"color: {COLORS['warning']}; font-size: 9pt;")
            self._confirm_btn.setText("Confirm Ticket")
            self._confirm_btn.setEnabled(True)
            self._refresh_start_button()

    # ── Manage Techs ─────────────────────────────────────────────

    def _open_manage_techs(self):
        """Open the Manage Techs dialog and refresh the tech selector after."""
        dlg = ManageTechsDialog(parent=self)
        dlg.exec()
        # Refresh the tech selector after dialog closes
        tech_names = get_tech_names()
        if tech_names:
            if self._tech_mode == 'combo' and self._name_combo:
                current = self._name_combo.currentText()
                self._name_combo.clear()
                for name in tech_names:
                    self._name_combo.addItem(name)
                if current in tech_names:
                    self._name_combo.setCurrentText(current)
            elif self._tech_mode == 'text' and self._name_input:
                # Switch to combo mode now that techs exist
                # (simpler: just note the hint text — full rebuild would require re-layout)
                self._name_input.setPlaceholderText(
                    "Restart app to use tech dropdown, or type name manually"
                )
        # If no techs configured, keep text mode as-is

    # ── Result helpers ────────────────────────────────────────────

    def _collect_results(self):
        """Pull values from UI into attributes."""
        if self._tech_mode == 'combo' and self._name_combo:
            self.tech_name = self._name_combo.currentText().strip()
        elif self._name_input:
            self.tech_name = self._name_input.text().strip()
        else:
            self.tech_name = ""  
        self.tech_notes  = self._notes_input.toPlainText().strip()
        self.report_type = (
            self.REPORT_TYPE_INITIAL if self._radio_initial.isChecked() else
            self.REPORT_TYPE_FINAL   if self._radio_final.isChecked() else
            ""
        )
        self.upload_scope = (
            UPLOAD_SCOPE_OVERVIEW if self._upload_overview_btn.isChecked()
            else UPLOAD_SCOPE_FULL
        )
        self.quick_upload_requested = self.upload_scope == UPLOAD_SCOPE_OVERVIEW
        if self.quick_upload_requested:
            self.skip_categories = set(self._category_checks.keys())
        else:
            self.skip_categories = {
                key for key, cb in self._category_checks.items()
                if not cb.isChecked()
            }
        # Save tech name for next session
        if self.tech_name:
            s = load_settings()
            s['last_tech_name'] = self.tech_name
            save_settings(s)

    def _set_all_categories(self, checked):
        """Select or deselect all diagnostic category checkboxes."""
        for checkbox in self._category_checks.values():
            checkbox.setChecked(checked)

    def _apply_job_setup_button_styles(self):
        self._skip_btn.setStyleSheet(
            f"background-color: {COLORS['card_bg']}; "
            f"color: {COLORS['text_primary']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 8px; padding: 0 18px; font-weight: bold; font-size: 12px;"
        )
        self._start_btn.setStyleSheet(
            f"background-color: {COLORS['primary']}; "
            f"color: {COLORS['text_white']}; "
            "border: none; border-radius: 8px; padding: 0 22px; "
            "font-weight: bold; font-size: 12px;"
        )

    def _refresh_start_button(self):
        """Update primary action wording based on upload mode and ticket confirmation."""
        if not hasattr(self, '_start_btn'):
            return

        if self.upload_scope == UPLOAD_SCOPE_OVERVIEW:
            if self.ticket_info:
                self._start_btn.setText("Quick Upload System Details  ▶")
            else:
                self._start_btn.setText("Confirm Ticket to Quick Upload  ▶")
        else:
            self._start_btn.setText("Start Full Scan  ▶")
        self._apply_job_setup_button_styles()

    # ── Button handlers ───────────────────────────────────────────

    def _on_start(self):
        """Validate report type then accept."""
        self.skip_scan_requested = False

        if self.upload_scope == UPLOAD_SCOPE_OVERVIEW:
            # Quick-upload path should be lightweight: assume an initial report
            # unless the tech explicitly chose the final-report option.
            if not self._radio_initial.isChecked() and not self._radio_final.isChecked():
                self._radio_initial.click()

            # If a ticket number was entered but not manually confirmed yet,
            # treat the quick-upload action as the confirmation trigger.
            if not self.ticket_info and self._ticket_input.text().strip():
                self._on_confirm_ticket()

            if not self.ticket_info:
                self._ticket_status.setText("⚠ Enter and confirm a ticket to quick-upload system details")
                self._ticket_status.setStyleSheet(
                    f"color: {COLORS['warning']}; font-size: 9pt;")
                return
        elif not self._radio_initial.isChecked() and not self._radio_final.isChecked():
            self._report_type_warning.setVisible(True)
            return

        self._report_type_warning.setVisible(False)
        self._collect_results()
        self.accept()

    def _on_skip(self):
        """Dismiss without starting a scan."""
        self.skip_scan_requested = True
        self._collect_results()
        self.reject()


class KeyboardTestKeyButton(QPushButton):
    """Keycap button that supports reset on click and mark-not-present on double click."""

    reset_requested = Signal(str)
    unavailable_toggled = Signal(str)

    def __init__(self, token, parent=None):
        super().__init__(token, parent)
        self._token = token
        self._suppress_release = False

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and not self._suppress_release:
            self.reset_requested.emit(self._token)
        self._suppress_release = False

    def mouseDoubleClickEvent(self, event):
        self._suppress_release = True
        self.unavailable_toggled.emit(self._token)
        event.accept()


class KeyboardTestDialog(QDialog):
    """Interactive onscreen keyboard test used during full scans."""

    DUPLICATE_THRESHOLD_MS = 150

    REQUIRED_KEYS = [
        'Esc', '`', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '=',
        'Backspace', 'Tab', 'Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P',
        '[', ']', '\\', 'Caps', 'A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L',
        ';', "'", 'Enter', 'LShift', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', ',',
        '.', '/', 'RShift', 'LCtrl', 'LAlt', 'Space', 'RAlt', 'RCtrl',
        '←', '↑', '↓', '→',
    ]

    DISPLAY_ROWS = [
        ['Esc', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'],
        ['Insert', 'Delete', 'Home', 'End', 'PageUp', 'PageDown'],
        ['`', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '=', 'Backspace'],
        ['Tab', 'Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', '[', ']', '\\'],
        ['Caps', 'A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', ';', "'", 'Enter'],
        ['LShift', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', ',', '.', '/', 'RShift', '↑'],
        ['LCtrl', 'LWin', 'LAlt', 'Space', 'RAlt', 'RWin', 'Menu', 'RCtrl', '←', '↓', '→'],
    ]

    KEY_SPANS = {
        'Backspace': 2.0,
        'Tab': 1.5,
        'Caps': 1.8,
        'Enter': 2.0,
        'LShift': 2.3,
        'RShift': 2.6,
        'LCtrl': 1.5,
        'RCtrl': 1.5,
        'LAlt': 1.5,
        'RAlt': 1.5,
        'LWin': 1.5,
        'RWin': 1.5,
        'Menu': 1.5,
        'Insert': 1.4,
        'Delete': 1.4,
        'Home': 1.4,
        'End': 1.4,
        'PageUp': 1.6,
        'PageDown': 1.8,
        'Space': 6.0,
    }

    OPTIONAL_TOKEN_MAP = {
        Qt.Key_F1: 'F1', Qt.Key_F2: 'F2', Qt.Key_F3: 'F3', Qt.Key_F4: 'F4',
        Qt.Key_F5: 'F5', Qt.Key_F6: 'F6', Qt.Key_F7: 'F7', Qt.Key_F8: 'F8',
        Qt.Key_F9: 'F9', Qt.Key_F10: 'F10', Qt.Key_F11: 'F11', Qt.Key_F12: 'F12',
        Qt.Key_Insert: 'Insert', Qt.Key_Delete: 'Delete', Qt.Key_Home: 'Home',
        Qt.Key_End: 'End', Qt.Key_PageUp: 'PageUp', Qt.Key_PageDown: 'PageDown',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Test")
        self.setModal(True)
        self.setMinimumSize(1040, 560)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")
        self.setFocusPolicy(Qt.StrongFocus)

        self._required_counts = {key: 0 for key in self.REQUIRED_KEYS}
        self._optional_counts = {}
        self._duplicate_keys = set()
        self._unavailable_keys = set()
        self._last_press_ms = {}
        self._key_labels = {}
        self.result_data = {
            'status': 'skipped',
            'summary': 'Test skipped',
            'required_keys_total': len(self.REQUIRED_KEYS),
            'registered_required_count': 0,
            'missing_keys': [],
            'duplicate_keys': [],
            'unavailable_keys': [],
            'optional_keys_pressed': [],
        }

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        title = QLabel("Keyboard Test")
        title.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 13px; font-weight: bold;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()
        outer.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(24, 22, 24, 22)
        body.setSpacing(14)

        instructions = QLabel(
            "Press each standard key once. Keys start grey, turn green after one clean press, and only turn red for near-instant duplicate bounce/ghost presses. "
            "When you finish, click Complete Keyboard Test to highlight anything that never registered.\n\n"
            "Click any key to reset it back to grey. Double-click a key to mark it as not present on this keyboard. "
            "Function-row keys shown here are optional and will not fail the test if they are absent or remapped by the OEM."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt;"
        )
        body.addWidget(instructions)

        self._summary_lbl = QLabel()
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 10pt; font-weight: bold;"
        )
        body.addWidget(self._summary_lbl)

        keyboard_card = QFrame()
        keyboard_card.setStyleSheet(
            f"background-color: {COLORS['card_bg']}; border: 1px solid {COLORS['card_border']}; border-radius: 10px;"
        )
        keyboard_layout = QVBoxLayout(keyboard_card)
        keyboard_layout.setContentsMargins(18, 18, 18, 18)
        keyboard_layout.setSpacing(10)

        for row_tokens in self.DISPLAY_ROWS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)
            for token in row_tokens:
                button = KeyboardTestKeyButton(token)
                button.setFocusPolicy(Qt.NoFocus)
                button.setCursor(Qt.PointingHandCursor)
                button.setFixedHeight(44)
                button.setMinimumWidth(int(48 * self.KEY_SPANS.get(token, 1.0)))
                button.setStyleSheet(self._build_key_style(token))
                button.reset_requested.connect(self._reset_key)
                button.unavailable_toggled.connect(self._toggle_key_unavailable)
                self._key_labels[token] = button
                row_layout.addWidget(button)
            row_layout.addStretch(1)
            keyboard_layout.addLayout(row_layout)

        body.addWidget(keyboard_card)

        self._optional_lbl = QLabel("Optional keys pressed: None yet")
        self._optional_lbl.setWordWrap(True)
        self._optional_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9.5pt;"
        )
        body.addWidget(self._optional_lbl)

        legend = QLabel("Grey = untested   •   Green = registered   •   Yellow = not present on this keyboard   •   Red = instant duplicate bounce or missing after completion")
        legend.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 9pt;"
        )
        body.addWidget(legend)

        body.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._skip_btn = QPushButton("Skip / Close")
        self._skip_btn.setObjectName("secondary")
        self._skip_btn.setCursor(Qt.PointingHandCursor)
        self._skip_btn.setFocusPolicy(Qt.NoFocus)
        self._skip_btn.clicked.connect(self._skip_test)
        btn_row.addWidget(self._skip_btn)

        self._complete_btn = QPushButton("Complete Keyboard Test")
        self._complete_btn.setObjectName("primary")
        self._complete_btn.setCursor(Qt.PointingHandCursor)
        self._complete_btn.setFocusPolicy(Qt.NoFocus)
        self._complete_btn.clicked.connect(self._complete_test)
        btn_row.addWidget(self._complete_btn)

        body.addLayout(btn_row)
        outer.addLayout(body)
        self._refresh_summary()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.setFocus)

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            event.accept()
            return

        token = self._event_to_token(event)
        if not token:
            super().keyPressEvent(event)
            return

        if token in self._required_counts:
            self._unavailable_keys.discard(token)
            now_ms = int(QDateTime.currentMSecsSinceEpoch())
            last_ms = self._last_press_ms.get(token)
            if self._required_counts[token] == 0:
                self._required_counts[token] = 1
            elif last_ms is not None and (now_ms - last_ms) <= self.DUPLICATE_THRESHOLD_MS:
                self._required_counts[token] = max(self._required_counts[token], 2)
                self._duplicate_keys.add(token)
            self._key_labels[token].setStyleSheet(self._build_key_style(token))
            self._last_press_ms[token] = now_ms
        else:
            self._optional_counts[token] = self._optional_counts.get(token, 0) + 1
            self._unavailable_keys.discard(token)
            if token in self._key_labels:
                self._key_labels[token].setStyleSheet(self._build_key_style(token))
            self._last_press_ms[token] = int(QDateTime.currentMSecsSinceEpoch())

        self._refresh_summary()
        event.accept()

    def reject(self):
        self._skip_test()

    def _skip_test(self):
        self.result_data = {
            'status': 'skipped',
            'summary': 'Test skipped',
            'required_keys_total': len(self.REQUIRED_KEYS),
            'registered_required_count': sum(
                1 for token, count in self._required_counts.items()
                if count > 0 or token in self._unavailable_keys
            ),
            'missing_keys': [],
            'duplicate_keys': [],
            'unavailable_keys': sorted(self._unavailable_keys),
            'optional_keys_pressed': sorted(self._optional_counts.keys()),
        }
        super().reject()

    def _complete_test(self):
        missing = [
            token for token, count in self._required_counts.items()
            if count == 0 and token not in self._unavailable_keys
        ]
        duplicates = sorted(self._duplicate_keys)

        for token in missing:
            self._key_labels[token].setStyleSheet(self._build_key_style(token, force_missing=True))

        if missing and duplicates:
            status = 'critical'
            summary = 'Issue - Missing and repeated keys detected'
        elif missing:
            status = 'critical'
            summary = 'Issue - Some Keys not registered'
        elif duplicates:
            status = 'warning'
            summary = 'Issue - Duplicate keypresses detected'
        else:
            status = 'ok'
            summary = 'All keys registered'

        self.result_data = {
            'status': status,
            'summary': summary,
            'required_keys_total': len(self.REQUIRED_KEYS),
            'registered_required_count': sum(
                1 for token, count in self._required_counts.items()
                if count > 0 or token in self._unavailable_keys
            ),
            'missing_keys': missing,
            'duplicate_keys': duplicates,
            'unavailable_keys': sorted(self._unavailable_keys),
            'optional_keys_pressed': sorted(self._optional_counts.keys()),
        }
        super().accept()

    def _refresh_summary(self):
        pressed = sum(
            1 for token, count in self._required_counts.items()
            if count > 0 or token in self._unavailable_keys
        )
        repeated = len(self._duplicate_keys)
        unavailable = len(self._unavailable_keys)
        self._summary_lbl.setText(
            f"Required keys registered: {pressed}/{len(self.REQUIRED_KEYS)}"
            + (f"  •  bounce duplicates: {repeated}" if repeated else "")
            + (f"  •  marked not present: {unavailable}" if unavailable else "")
        )

        if self._optional_counts:
            optional_text = ", ".join(sorted(self._optional_counts.keys()))
            self._optional_lbl.setText(f"Optional keys pressed: {optional_text}")
        else:
            self._optional_lbl.setText("Optional keys pressed: None yet")

    def _build_key_style(self, token, force_missing=False):
        count = self._required_counts.get(token, self._optional_counts.get(token, 0))
        if token in self._unavailable_keys:
            background = "#92400E"
            border = "#F59E0B"
            text = "#FEF3C7"
        elif force_missing or count == 0:
            background = "#4B5563" if not force_missing else "#991B1B"
            border = "#6B7280" if not force_missing else "#EF4444"
            text = "#F9FAFB"
        elif count == 1:
            background = "#065F46"
            border = "#10B981"
            text = "#F9FAFB"
        else:
            background = "#991B1B"
            border = "#EF4444"
            text = "#FDE68A"

        return (
            f"background-color: {background}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 6px; "
            "font-size: 10pt; font-weight: bold; padding: 4px 6px;"
        )

    def _reset_key(self, token):
        if token not in self._key_labels:
            return
        if token in self._required_counts:
            self._required_counts[token] = 0
        self._optional_counts.pop(token, None)
        self._duplicate_keys.discard(token)
        self._unavailable_keys.discard(token)
        self._last_press_ms.pop(token, None)
        self._key_labels[token].setStyleSheet(self._build_key_style(token))
        self._refresh_summary()
        QTimer.singleShot(0, self.setFocus)

    def _toggle_key_unavailable(self, token):
        if token not in self._key_labels:
            return
        if token in self._unavailable_keys:
            self._unavailable_keys.discard(token)
        else:
            self._unavailable_keys.add(token)
            if token in self._required_counts:
                self._required_counts[token] = 0
            self._optional_counts.pop(token, None)
            self._duplicate_keys.discard(token)
            self._last_press_ms.pop(token, None)
        self._key_labels[token].setStyleSheet(self._build_key_style(token))
        self._refresh_summary()
        QTimer.singleShot(0, self.setFocus)

    @staticmethod
    def _modifier_token_from_native_codes(key, scancode, virtual_key):
        if virtual_key == 0xA3:
            return 'RCtrl'
        if virtual_key == 0xA2:
            return 'LCtrl'
        if virtual_key == 0x11:
            return 'RCtrl' if scancode in (285, 3613) else 'LCtrl'
        if virtual_key == 0xA5:
            return 'RAlt'
        if virtual_key == 0xA4:
            return 'LAlt'
        if virtual_key == 0x12:
            return 'RAlt' if scancode in (312, 3640) else 'LAlt'
        if virtual_key == 0xA1:
            return 'RShift'
        if virtual_key == 0xA0:
            return 'LShift'
        if virtual_key == 0x5C:
            return 'RWin'
        if virtual_key == 0x5B:
            return 'LWin'

        scan_code_map = {
            29: 'LCtrl',
            285: 'RCtrl',
            3613: 'RCtrl',
            56: 'LAlt',
            312: 'RAlt',
            3640: 'RAlt',
            42: 'LShift',
            54: 'RShift',
            347: 'LWin',
            3675: 'LWin',
            348: 'RWin',
            3676: 'RWin',
        }
        if scancode in scan_code_map:
            return scan_code_map[scancode]

        if key == Qt.Key_Control:
            return 'RCtrl' if scancode in (285, 3613) else 'LCtrl'
        if key == Qt.Key_Alt:
            return 'RAlt' if scancode in (312, 3640) else 'LAlt'
        if key == Qt.Key_AltGr:
            return 'RAlt'
        if key == Qt.Key_Shift:
            return 'RShift' if scancode in (54,) else 'LShift'
        if key == Qt.Key_Meta:
            if scancode in (348, 3676):
                return 'RWin'
            if scancode in (347, 3675):
                return 'LWin'
            return None
        return None

    def _event_to_token(self, event):
        key = event.key()
        modifiers = event.modifiers()
        scancode = event.nativeScanCode()
        virtual_key = event.nativeVirtualKey()

        if modifiers & Qt.KeypadModifier:
            keypad_map = {
                Qt.Key_0: 'Num 0', Qt.Key_1: 'Num 1', Qt.Key_2: 'Num 2',
                Qt.Key_3: 'Num 3', Qt.Key_4: 'Num 4', Qt.Key_5: 'Num 5',
                Qt.Key_6: 'Num 6', Qt.Key_7: 'Num 7', Qt.Key_8: 'Num 8',
                Qt.Key_9: 'Num 9', Qt.Key_Plus: 'Num +', Qt.Key_Minus: 'Num -',
                Qt.Key_Asterisk: 'Num *', Qt.Key_Slash: 'Num /',
                Qt.Key_Period: 'Num .', Qt.Key_Enter: 'Num Enter',
            }
            if key in keypad_map:
                return keypad_map[key]

        if Qt.Key_A <= key <= Qt.Key_Z:
            return chr(ord('A') + (key - Qt.Key_A))
        if Qt.Key_0 <= key <= Qt.Key_9:
            return chr(ord('0') + (key - Qt.Key_0))

        modifier_token = self._modifier_token_from_native_codes(key, scancode, virtual_key)
        if modifier_token:
            return modifier_token

        special_map = {
            Qt.Key_Escape: 'Esc',
            Qt.Key_QuoteLeft: '`',
            Qt.Key_AsciiTilde: '`',
            Qt.Key_Minus: '-',
            Qt.Key_Equal: '=',
            Qt.Key_Backspace: 'Backspace',
            Qt.Key_Tab: 'Tab',
            Qt.Key_BracketLeft: '[',
            Qt.Key_BracketRight: ']',
            Qt.Key_Backslash: '\\',
            Qt.Key_CapsLock: 'Caps',
            Qt.Key_Semicolon: ';',
            Qt.Key_Apostrophe: "'",
            Qt.Key_Return: 'Enter',
            Qt.Key_Enter: 'Enter',
            Qt.Key_Comma: ',',
            Qt.Key_Period: '.',
            Qt.Key_Slash: '/',
            Qt.Key_Menu: 'Menu',
            Qt.Key_Space: 'Space',
            Qt.Key_Left: '←',
            Qt.Key_Up: '↑',
            Qt.Key_Down: '↓',
            Qt.Key_Right: '→',
        }
        if key in special_map:
            return special_map[key]

        if key in self.OPTIONAL_TOKEN_MAP:
            return self.OPTIONAL_TOKEN_MAP[key]

        text = (event.text() or '').strip()
        if text:
            return text.upper()
        return None

# ─── Welcome / First-Run Dialog ─────────────────────────────────────

class WelcomeDialog(QDialog):
    """First-run setup dialog for store, RepairDesk, and optional shop WiFi settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = load_settings()
        self._oauth_connected = oauth_is_connected(self._settings)

        self.setWindowTitle(f"{APP_NAME} — First Use Setup")
        self.setFixedSize(560, 620)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(14)

        # Title
        title = QLabel("First Use Setup")
        title.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 18px; "
            "font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Enter your shop details before the first scan. "
            "WiFi is optional and can be added later in Settings."
        )
        subtitle.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px;")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(4)

        # Store name
        store_label = QLabel("Store Name")
        store_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            "font-weight: bold;")
        layout.addWidget(store_label)

        self._store_input = QLineEdit()
        self._store_input.setPlaceholderText("Your shop name")
        self._store_input.setFixedHeight(36)
        self._store_input.setText(self._settings.get('store_name', ''))
        layout.addWidget(self._store_input)

        # Auth mode
        auth_label = QLabel("RepairDesk Authentication")
        auth_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            "font-weight: bold;")
        layout.addWidget(auth_label)

        self._auth_mode_combo = QComboBox()
        self._auth_mode_combo.addItem("API Key", "api_key")
        self._auth_mode_combo.addItem("OAuth 2.0", "oauth")
        current_auth_mode = self._settings.get('auth_mode', 'api_key')
        self._auth_mode_combo.setCurrentIndex(1 if current_auth_mode == 'oauth' else 0)
        self._auth_mode_combo.currentIndexChanged.connect(self._sync_auth_mode_ui)
        layout.addWidget(self._auth_mode_combo)

        # API key
        key_label = QLabel("RepairDesk API Key")
        key_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            "font-weight: bold;")
        self._api_key_label = key_label
        layout.addWidget(key_label)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("Paste your API key")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setFixedHeight(36)
        self._key_input.setText(self._settings.get('api_key', ''))

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(self._key_input, 1)

        self._show_key_cb = QCheckBox("Show")
        self._show_key_cb.setStyleSheet("border: none;")
        self._show_key_cb.toggled.connect(
            lambda checked: self._key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self._show_key_cb)
        self._api_key_row = key_row
        layout.addLayout(key_row)

        self._oauth_body = QWidget()
        oauth_layout = QVBoxLayout(self._oauth_body)
        oauth_layout.setContentsMargins(0, 0, 0, 0)
        oauth_layout.setSpacing(8)

        client_row = QHBoxLayout()
        client_row.setSpacing(8)
        client_lbl = QLabel("Client ID")
        client_lbl.setFixedWidth(90)
        client_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold;")
        client_row.addWidget(client_lbl)
        self._oauth_client_id_input = QLineEdit()
        self._oauth_client_id_input.setText(self._settings.get('oauth_client_id', ''))
        self._oauth_client_id_input.setPlaceholderText("RepairDesk OAuth client ID")
        client_row.addWidget(self._oauth_client_id_input)
        oauth_layout.addLayout(client_row)

        secret_row = QHBoxLayout()
        secret_row.setSpacing(8)
        secret_lbl = QLabel("Client Secret")
        secret_lbl.setFixedWidth(90)
        secret_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold;")
        secret_row.addWidget(secret_lbl)
        self._oauth_client_secret_input = QLineEdit()
        self._oauth_client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._oauth_client_secret_input.setText(self._settings.get('oauth_client_secret', ''))
        self._oauth_client_secret_input.setPlaceholderText("RepairDesk OAuth client secret")
        secret_row.addWidget(self._oauth_client_secret_input)
        self._show_oauth_secret_cb = QCheckBox("Show")
        self._show_oauth_secret_cb.setStyleSheet("border: none;")
        self._show_oauth_secret_cb.toggled.connect(
            lambda checked: self._oauth_client_secret_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        secret_row.addWidget(self._show_oauth_secret_cb)
        oauth_layout.addLayout(secret_row)

        redirect_row = QHBoxLayout()
        redirect_row.setSpacing(8)
        redirect_lbl = QLabel("Redirect URI")
        redirect_lbl.setFixedWidth(90)
        redirect_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold;")
        redirect_row.addWidget(redirect_lbl)
        self._oauth_redirect_input = QLineEdit()
        self._oauth_redirect_input.setText(
            self._settings.get('oauth_redirect_uri', DEFAULTS['oauth_redirect_uri'])
        )
        redirect_row.addWidget(self._oauth_redirect_input)
        oauth_layout.addLayout(redirect_row)

        oauth_btn_row = QHBoxLayout()
        oauth_btn_row.setSpacing(8)
        self._oauth_connect_btn = QPushButton("Connect OAuth")
        self._oauth_connect_btn.setObjectName("secondary")
        self._oauth_connect_btn.setCursor(Qt.PointingHandCursor)
        self._oauth_connect_btn.clicked.connect(self._on_connect_oauth)
        oauth_btn_row.addWidget(self._oauth_connect_btn)

        self._oauth_disconnect_btn = QPushButton("Disconnect")
        self._oauth_disconnect_btn.setObjectName("secondary")
        self._oauth_disconnect_btn.setCursor(Qt.PointingHandCursor)
        self._oauth_disconnect_btn.clicked.connect(self._on_disconnect_oauth)
        oauth_btn_row.addWidget(self._oauth_disconnect_btn)
        oauth_btn_row.addStretch()
        oauth_layout.addLayout(oauth_btn_row)

        self._oauth_status = QLabel()
        self._oauth_status.setWordWrap(True)
        self._oauth_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        oauth_layout.addWidget(self._oauth_status)

        oauth_help = QLabel(
            "OAuth opens RepairDesk in your browser so you can approve PC AutoSpec."
        )
        oauth_help.setWordWrap(True)
        oauth_help.setObjectName("hint")
        oauth_layout.addWidget(oauth_help)

        layout.addWidget(self._oauth_body)
        if self._oauth_connected:
            self._oauth_status.setText("OAuth connected successfully.")
            self._oauth_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 9pt;")

        # WiFi SSID
        wifi_label = QLabel("Shop WiFi SSID  (optional)")
        wifi_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            "font-weight: bold;")
        layout.addWidget(wifi_label)

        self._wifi_ssid_input = QLineEdit()
        self._wifi_ssid_input.setPlaceholderText("Guest or shop WiFi name")
        self._wifi_ssid_input.setFixedHeight(36)
        self._wifi_ssid_input.setText(self._settings.get('wifi_ssid', ''))
        layout.addWidget(self._wifi_ssid_input)

        # WiFi password
        wifi_pass_label = QLabel("Shop WiFi Password  (optional)")
        wifi_pass_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            "font-weight: bold;")
        layout.addWidget(wifi_pass_label)

        self._wifi_pass_input = QLineEdit()
        self._wifi_pass_input.setPlaceholderText("WiFi password")
        self._wifi_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._wifi_pass_input.setFixedHeight(36)
        self._wifi_pass_input.setText(self._settings.get('wifi_password', ''))

        wifi_pass_row = QHBoxLayout()
        wifi_pass_row.setSpacing(8)
        wifi_pass_row.addWidget(self._wifi_pass_input, 1)

        self._show_wifi_cb = QCheckBox("Show")
        self._show_wifi_cb.setStyleSheet("border: none;")
        self._show_wifi_cb.toggled.connect(
            lambda checked: self._wifi_pass_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        wifi_pass_row.addWidget(self._show_wifi_cb)
        layout.addLayout(wifi_pass_row)

        hint = QLabel(
            "WiFi auto-connect is only used when the scanned machine has no internet. "
            "You can update these values later in Settings."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()

        # Save button
        self._save_btn = QPushButton("Save and Continue")
        self._save_btn.setObjectName("primary")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setFixedHeight(44)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn)

        # Enable save when required fields have content
        self._store_input.textChanged.connect(self._check_fields)
        self._key_input.textChanged.connect(self._check_fields)
        self._oauth_client_id_input.textChanged.connect(self._check_fields)
        self._oauth_client_secret_input.textChanged.connect(self._check_fields)
        self._oauth_redirect_input.textChanged.connect(self._check_fields)
        self._wifi_ssid_input.textChanged.connect(self._check_fields)
        self._wifi_pass_input.textChanged.connect(self._check_fields)
        self._sync_auth_mode_ui()
        self._check_fields()

    def _check_fields(self):
        has_store = len(self._store_input.text().strip()) > 0
        if self._auth_mode_combo.currentData() == 'oauth':
            has_auth = self._oauth_connected
        else:
            has_auth = len(self._key_input.text().strip()) > 5
        self._save_btn.setEnabled(has_store and has_auth)

    def _sync_auth_mode_ui(self):
        use_oauth = self._auth_mode_combo.currentData() == 'oauth'
        self._api_key_label.setVisible(not use_oauth)
        self._key_input.setVisible(not use_oauth)
        self._show_key_cb.setVisible(not use_oauth)
        self._oauth_body.setVisible(use_oauth)
        self._check_fields()

    def _on_connect_oauth(self):
        oauth_settings = dict(self._settings)
        oauth_settings['auth_mode'] = 'oauth'
        oauth_settings['oauth_client_id'] = self._oauth_client_id_input.text().strip()
        oauth_settings['oauth_client_secret'] = self._oauth_client_secret_input.text().strip()
        oauth_settings['oauth_redirect_uri'] = (
            self._oauth_redirect_input.text().strip() or DEFAULTS['oauth_redirect_uri']
        )
        try:
            self._oauth_status.setText("Opening RepairDesk sign-in...")
            QApplication.processEvents()
            run_oauth_flow(oauth_settings)
            self._oauth_connected = True
            self._settings = load_settings()
            self._oauth_status.setText("OAuth connected successfully.")
            self._oauth_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 9pt;")
        except Exception as e:
            self._oauth_connected = False
            self._oauth_status.setText(str(e))
            self._oauth_status.setStyleSheet(f"color: {COLORS['error']}; font-size: 9pt;")
        self._check_fields()

    def _on_disconnect_oauth(self):
        settings = load_settings()
        clear_oauth_tokens(settings)
        self._oauth_connected = False
        self._oauth_status.setText("OAuth connection cleared.")
        self._oauth_status.setStyleSheet(f"color: {COLORS['warning']}; font-size: 9pt;")
        self._check_fields()

    def _on_save(self):
        wifi_ssid = self._wifi_ssid_input.text().strip()
        wifi_password = self._wifi_pass_input.text().strip()
        settings = load_settings()
        settings['store_name'] = self._store_input.text().strip()
        settings['auth_mode'] = self._auth_mode_combo.currentData()
        settings['api_key'] = self._key_input.text().strip()
        settings['oauth_client_id'] = self._oauth_client_id_input.text().strip()
        settings['oauth_client_secret'] = self._oauth_client_secret_input.text().strip()
        settings['oauth_redirect_uri'] = (
            self._oauth_redirect_input.text().strip() or DEFAULTS['oauth_redirect_uri']
        )
        settings['wifi_ssid'] = wifi_ssid
        settings['wifi_password'] = wifi_password
        settings['wifi_auto_connect'] = bool(wifi_ssid)
        save_settings(settings)
        self.accept()


# ─── Launch Dialog ───────────────────────────────────────────────────

class LaunchDialog(QDialog):
    """Shown every time the app launches — captures tech name and ticket number."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tech_name = ""
        self.ticket_number = ""

        self.setWindowTitle(f"{APP_NAME} — Start Scan")
        self.setFixedSize(420, 260)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        # Title
        title = QLabel("Start New Scan")
        title.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your name and the RepairDesk ticket number.")
        subtitle.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px;")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(4)

        # Tech name
        name_lbl = QLabel("Your Name")
        name_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold;")
        layout.addWidget(name_lbl)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("First name")
        self._name_input.setFixedHeight(38)
        # Remember last used name
        s = load_settings()
        self._name_input.setText(s.get('last_tech_name', ''))
        layout.addWidget(self._name_input)

        # Ticket number
        ticket_lbl = QLabel("Ticket Number")
        ticket_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold;")
        layout.addWidget(ticket_lbl)

        self._ticket_input = QLineEdit()
        self._ticket_input.setPlaceholderText("e.g. 15108 or T-15108")
        self._ticket_input.setFixedHeight(38)
        layout.addWidget(self._ticket_input)

        layout.addStretch()

        # Start button
        self._start_btn = QPushButton("Start Scan")
        self._start_btn.setObjectName("primary")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setFixedHeight(44)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        layout.addWidget(self._start_btn)

        self._name_input.textChanged.connect(self._check_fields)
        self._ticket_input.textChanged.connect(self._check_fields)
        self._ticket_input.returnPressed.connect(self._start_btn.click)

    def _check_fields(self):
        has_name = len(self._name_input.text().strip()) > 0
        has_ticket = len(self._ticket_input.text().strip()) > 0
        self._start_btn.setEnabled(has_name and has_ticket)

    def _on_start(self):
        self.tech_name = self._name_input.text().strip()
        raw = self._ticket_input.text().strip().lstrip('Tt-').strip()
        self.ticket_number = raw
        # Remember tech name for next time
        s = load_settings()
        s['last_tech_name'] = self.tech_name
        save_settings(s)
        self.accept()


# ─── Detail Drill-Down Dialog ────────────────────────────────────────

class DetailDialog(QDialog):
    """Popup showing detailed breakdown for a diagnostic section.

    Usage:
        dlg = DetailDialog("Event Log", parent=self)
        dlg.add_row("Total Events", "18", color="#EF4444")
        dlg.add_heading("Top Sources")
        dlg.add_row("DistributedCOM", "7 events")
        dlg.add_row("WHEA-Logger", "5 events")
        dlg.add_text("Latest critical: WHEA-Logger at 2026-02-27 ...")
        dlg.exec()
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Detail \u2014 {title}")
        self.resize(720, 550)
        self.setMinimumSize(560, 400)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 13px; "
            f"font-weight: bold;")
        h_lay.addWidget(lbl)
        outer.addWidget(header)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {COLORS['bg_root']}; }}")
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(20, 12, 20, 12)
        self._content_layout.setSpacing(0)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)

        self._row_count = 0

        # Close button
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(20, 8, 20, 16)
        btn_bar.addStretch()
        self._btn_bar = btn_bar
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        self._btn_bar.addWidget(close_btn)
        outer.addLayout(btn_bar)

    def add_row(self, label, value, color=None):
        """Add a label:value row with alternating background."""
        bg = COLORS['card_bg'] if self._row_count % 2 == 0 else COLORS['row_alt']
        row = QWidget()
        row.setStyleSheet(f"background-color: {bg}; border-radius: 0px;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(8)

        lbl = QLabel(f"{label}:")
        lbl.setObjectName("rowLabelBold")
        lbl.setMinimumWidth(140)
        lbl.setMaximumWidth(220)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(lbl)

        val = QLabel(str(value))
        val.setObjectName("rowValue")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        val.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        if color:
            val.setStyleSheet(f"color: {color};")
        lay.addWidget(val, 1)

        self._content_layout.addWidget(row)
        self._row_count += 1

    def add_heading(self, text):
        """Add a bold sub-heading to group rows."""
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: bold; "
            f"color: {COLORS['primary']}; "
            f"padding: 10px 8px 4px 8px;")
        self._content_layout.addWidget(lbl)

    def add_text(self, text, color=None):
        """Add a plain text line (wrapping, selectable)."""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        style = f"padding: 4px 8px; font-size: 11px;"
        if color:
            style += f" color: {color};"
        else:
            style += f" color: {COLORS['text_secondary']};"
        lbl.setStyleSheet(style)
        self._content_layout.addWidget(lbl)

    def add_separator(self):
        """Add a thin horizontal line."""
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(
            f"background-color: {COLORS['border']}; border: none;")
        self._content_layout.addWidget(sep)

    def add_action_button(self, label, callback, object_name="primary"):
        """Add an action button to the footer before the Close button."""
        btn = QPushButton(label)
        btn.setObjectName(object_name)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(callback)
        insert_at = max(0, self._btn_bar.count() - 1)
        self._btn_bar.insertWidget(insert_at, btn)
        return btn


# ─── Report Preview Dialog ───────────────────────────────────────────

class ReportPreviewDialog(QDialog):
    """Rendered HTML preview matching RepairDesk's note display.

    Shows the diagnostic report as it will appear in RepairDesk.
    Editable — user enters ticket number and uploads from here.

    Usage:
        dlg = ReportPreviewDialog(specs, formatter, parent)
        if dlg.exec() == QDialog.Accepted:
            ticket_id = dlg.ticket_id    # e.g. "T-12345"
            edited_html = dlg.edited_html
    """

    def __init__(self, specs, formatter, issues=None, parent=None,
                 prefill_ticket='', prefill_tech_name='', prefill_notes='',
                 ticket_already_confirmed=False,
                 initial_upload_scope=DEFAULT_UPLOAD_SCOPE):
        super().__init__(parent)
        self.setWindowTitle("Scan Summary / Upload")
        self.resize(920, 780)
        self.setMinimumSize(700, 550)

        self._specs = dict(specs or {})
        self._formatter = formatter
        self._original_html = ""
        self.edited_html = ""
        self.ticket_id = prefill_ticket or None
        self.tech_name = prefill_tech_name or ""
        self.tech_notes = prefill_notes or ""
        self.upload_scope = initial_upload_scope or DEFAULT_UPLOAD_SCOPE
        self._issues = issues or []
        self._ticket_already_confirmed = ticket_already_confirmed

        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 12)
        layout.setSpacing(6)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(50)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)
        title = QLabel("Scan Summary / Upload")
        title.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 13px; font-weight: bold;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        hint = QLabel("Review findings, add notes, then upload to RepairDesk")
        hint.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 9pt;")
        h_layout.addWidget(hint)
        layout.addWidget(header)

        # ── Job details summary ──────────────────────────────────
        job_card = QFrame()
        job_card.setObjectName("card")
        job_layout = QVBoxLayout(job_card)
        job_layout.setContentsMargins(14, 12, 14, 12)
        job_layout.setSpacing(8)

        job_header = QHBoxLayout()
        job_header.setSpacing(8)
        job_title = QLabel("Job Details")
        job_title.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 10pt; font-weight: bold;")
        job_header.addWidget(job_title)
        job_header.addStretch()

        self._details_toggle_btn = QPushButton()
        self._details_toggle_btn.setObjectName("secondary")
        self._details_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._details_toggle_btn.clicked.connect(self._toggle_details_editor)
        job_header.addWidget(self._details_toggle_btn)
        job_layout.addLayout(job_header)

        self._job_summary = QLabel()
        self._job_summary.setWordWrap(True)
        self._job_summary.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9.5pt;")
        job_layout.addWidget(self._job_summary)

        self._details_editor = QWidget()
        details_layout = QVBoxLayout(self._details_editor)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(10)

        # ── Tech name + ticket row ───────────────────────────────
        fields_row = QHBoxLayout()
        fields_row.setContentsMargins(0, 0, 0, 0)
        fields_row.setSpacing(16)

        name_lbl = QLabel("Tech Name:")
        name_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
        name_lbl.setFixedWidth(80)
        fields_row.addWidget(name_lbl)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Your first name")
        self._name_input.setFixedHeight(34)
        self._name_input.setFixedWidth(160)
        s = load_settings()
        self._name_input.setText(prefill_tech_name or s.get('last_tech_name', ''))
        self._name_input.textChanged.connect(self._on_fields_changed)
        fields_row.addWidget(self._name_input)

        fields_row.addSpacing(8)

        ticket_lbl = QLabel("Ticket #:")
        ticket_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
        ticket_lbl.setFixedWidth(60)
        fields_row.addWidget(ticket_lbl)

        self._ticket_input = QLineEdit()
        self._ticket_input.setPlaceholderText("e.g. 15108")
        self._ticket_input.setFixedHeight(34)
        self._ticket_input.setFixedWidth(160)
        if prefill_ticket:
            self._ticket_input.setText(prefill_ticket)
        self._ticket_input.textChanged.connect(self._on_ticket_changed)
        fields_row.addWidget(self._ticket_input)

        fields_row.addStretch()

        self._info_label = QLabel()
        self._info_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        fields_row.addWidget(self._info_label)
        details_layout.addLayout(fields_row)

        job_layout.addWidget(self._details_editor)
        layout.addWidget(job_card)

        # ── Critical Issues panel ────────────────────────────────
        if self._issues:
            issues_widget = QWidget()
            issues_widget.setStyleSheet(
                "background-color: #2D1515; border: 1px solid #EF4444; border-radius: 6px;")
            issues_layout = QVBoxLayout(issues_widget)
            issues_layout.setContentsMargins(14, 10, 14, 10)
            issues_layout.setSpacing(4)

            issues_title = QLabel("⚠  CRITICAL ISSUES")
            issues_title.setStyleSheet(
                "color: #EF4444; font-size: 11px; font-weight: bold; border: none;")
            issues_layout.addWidget(issues_title)

            for issue in self._issues:
                row = QLabel(f"• {issue}")
                row.setStyleSheet(
                    "color: #FCA5A5; font-size: 11px; border: none;")
                row.setWordWrap(True)
                issues_layout.addWidget(row)

            layout.addWidget(issues_widget)

        # ── Tech Notes ───────────────────────────────────────────
        notes_lbl = QLabel("Tech Notes  (optional — will be included in the report)")
        notes_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
        layout.addWidget(notes_lbl)

        self._notes_input = QTextEdit()
        self._notes_input.setPlaceholderText(
            "e.g. noticed malware popups on startup, crack near charging port, fan making grinding noise...")
        if prefill_notes:
            self._notes_input.setPlainText(prefill_notes)
        self._notes_input.setFixedHeight(72)
        self._notes_input.setStyleSheet(
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 8px; font-size: 10pt;")
        layout.addWidget(self._notes_input)

        # ── Upload scope ────────────────────────────────────────
        scope_lbl = QLabel("Upload Content")
        scope_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
        layout.addWidget(scope_lbl)

        btn_base = (
            "QPushButton {"
            "  border: 2px solid #374151;"
            "  border-radius: 8px;"
            "  padding: 10px 18px;"
            "  font-size: 10.5pt;"
            "  font-weight: bold;"
            "  color: #9CA3AF;"
            "  background-color: #1F2937;"
            "}"
            "QPushButton:hover {"
            "  border-color: #6B7280;"
            "  color: #D1D5DB;"
            "  background-color: #374151;"
            "}"
        )
        btn_selected = (
            "QPushButton {"
            "  border: 2px solid #10B981;"
            "  border-radius: 8px;"
            "  padding: 10px 18px;"
            "  font-size: 10.5pt;"
            "  font-weight: bold;"
            "  color: #ffffff;"
            "  background-color: #065F46;"
            "}"
        )

        self._upload_scope_base = btn_base
        self._upload_scope_selected = btn_selected

        scope_row = QHBoxLayout()
        scope_row.setSpacing(12)

        self._upload_overview_btn = QPushButton("Upload System Overview only")
        self._upload_overview_btn.setCheckable(True)
        self._upload_overview_btn.setFixedHeight(46)
        self._upload_overview_btn.clicked.connect(
            lambda: self._set_upload_scope(UPLOAD_SCOPE_OVERVIEW))
        scope_row.addWidget(self._upload_overview_btn)

        self._upload_full_btn = QPushButton("Upload full results")
        self._upload_full_btn.setCheckable(True)
        self._upload_full_btn.setFixedHeight(46)
        self._upload_full_btn.clicked.connect(
            lambda: self._set_upload_scope(UPLOAD_SCOPE_FULL))
        scope_row.addWidget(self._upload_full_btn)

        layout.addLayout(scope_row)

        self._scope_hint = QLabel()
        self._scope_hint.setWordWrap(True)
        self._scope_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        layout.addWidget(self._scope_hint)

        # ── Report preview ───────────────────────────────────────
        preview_lbl = QLabel("Report Preview")
        preview_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10pt; font-weight: bold;")
        layout.addWidget(preview_lbl)

        self._editor = QTextEdit()
        self._editor.setStyleSheet(
            "background-color: #FFFFFF; "
            "color: #111827; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 8px; padding: 16px; "
            "font-family: 'Segoe UI', 'Arial', sans-serif; "
            "font-size: 10pt; "
            f"selection-background-color: {COLORS['primary']};")
        self._editor.document().setDefaultStyleSheet("""
            body, div, p, span {
                color: #111827;
                background-color: #FFFFFF;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 10pt;
                line-height: 1.3;
            }
            strong {
                color: #111827;
                font-weight: 700;
            }
            ul, ol {
                margin-top: 0;
                margin-bottom: 0;
            }
            li {
                margin: 0;
            }
        """)
        layout.addWidget(self._editor, 1)
        layout.setContentsMargins(16, 0, 16, 0)
        self._editor.textChanged.connect(self._update_char_count)
        self._set_upload_scope(self.upload_scope, rebuild=False)
        self._rebuild_preview()
        self._details_collapsed = bool(prefill_ticket or prefill_tech_name)
        self._sync_details_editor()

        # ── Upload button ────────────────────────────────────────
        self._upload_btn = QPushButton()
        self._upload_btn.setCursor(Qt.PointingHandCursor)
        self._upload_btn.setFixedHeight(48)
        self._upload_btn.clicked.connect(self._on_upload_clicked)
        layout.addWidget(self._upload_btn)
        self._update_upload_button()

        # ── Secondary buttons row ────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 4, 0, 8)
        btn_bar.setSpacing(8)
        btn_bar.addStretch()

        self._copy_btn = QPushButton("Copy HTML")
        self._copy_btn.setObjectName("secondary")
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.clicked.connect(self._on_copy)
        btn_bar.addWidget(self._copy_btn)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        btn_bar.addWidget(close_btn)

        layout.addLayout(btn_bar)

    def _set_upload_scope(self, scope, rebuild=True):
        """Apply the active upload mode and optionally regenerate the preview."""
        self.upload_scope = scope
        overview_selected = scope == UPLOAD_SCOPE_OVERVIEW
        self._upload_overview_btn.setChecked(overview_selected)
        self._upload_full_btn.setChecked(not overview_selected)
        self._upload_overview_btn.setStyleSheet(
            self._upload_scope_selected if overview_selected else self._upload_scope_base)
        self._upload_full_btn.setStyleSheet(
            self._upload_scope_selected if not overview_selected else self._upload_scope_base)

        if overview_selected:
            self._scope_hint.setText(
                "Uploads only OS version, CPU, RAM, and drive type/capacity usage."
            )
        else:
            self._scope_hint.setText(
                "Uploads the full diagnostic report, including detailed hardware and diagnostics."
            )

        self._update_job_summary()
        if rebuild:
            self._rebuild_preview()

    def _rebuild_preview(self):
        """Regenerate the preview HTML from the current specs and upload scope."""
        note_html = self._formatter.format_diagnostic_note(
            self._specs,
            upload_mode=self.upload_scope,
        )
        self._original_html = note_html
        self.edited_html = note_html
        self._editor.blockSignals(True)
        self._editor.setHtml(note_html)
        self._editor.document().setModified(False)
        self._editor.blockSignals(False)
        self._update_char_count()

    # ── HTML extraction ──────────────────────────────────────────

    def _extract_html(self):
        """Get clean HTML suitable for RepairDesk from the editor."""
        if not self._editor.document().isModified():
            return self._original_html
        return self._clean_qt_html(self._editor.toHtml())

    @staticmethod
    def _clean_qt_html(qt_html):
        """Convert Qt's verbose toHtml() back to simple <strong>/<br> HTML.

        Qt wraps content in a full HTML document with inline CSS.
        RepairDesk just needs <strong> tags and <br> line breaks.
        """
        # Extract body content
        body_m = re.search(r'<body[^>]*>(.*)</body>', qt_html, re.DOTALL)
        html = body_m.group(1).strip() if body_m else qt_html

        # Convert Qt's bold spans to <strong>
        # Qt uses: <span style=" font-weight:700;">text</span>
        html = re.sub(
            r'<span\s+style="[^"]*font-weight:\s*(?:bold|[6-9]\d{2})[^"]*">'
            r'(.*?)</span>',
            r'<strong>\1</strong>', html, flags=re.DOTALL)

        # Remove remaining span tags (keep their content)
        html = re.sub(r'</?span[^>]*>', '', html)

        # Convert paragraph breaks to <br>
        html = re.sub(r'</p>\s*<p[^>]*>', '<br>', html)

        # Remove opening/closing <p> tags
        html = re.sub(r'<p[^>]*>', '', html)
        html = re.sub(r'</p>', '', html)

        # Normalise consecutive <br> tags
        html = re.sub(r'(<br\s*/?\s*>){3,}', '<br><br>', html)

        # Strip leading/trailing whitespace and <br>
        html = html.strip()
        while html.startswith('<br>'):
            html = html[4:].lstrip()
        while html.endswith('<br>'):
            html = html[:-4].rstrip()

        return html

    # ── UI callbacks ─────────────────────────────────────────────

    def _on_ticket_changed(self, text):
        self._update_job_summary()
        self._update_upload_button()

    def _on_fields_changed(self, text):
        self._specs['_job_tech_name'] = self._name_input.text().strip()
        self._update_job_summary()
        self._rebuild_preview()
        self._update_upload_button()

    def _toggle_details_editor(self):
        self._details_collapsed = not self._details_collapsed
        self._sync_details_editor()

    def _sync_details_editor(self):
        self._details_editor.setVisible(not self._details_collapsed)
        self._details_toggle_btn.setText(
            "Edit Details" if self._details_collapsed else "Hide Details"
        )
        self._update_job_summary()

    def _update_job_summary(self):
        tech = self._name_input.text().strip() or "Not set"
        ticket = self._ticket_input.text().strip() or "Not set"
        report_type = self._specs.get('_job_report_type') or "Not set"
        scope = "System Overview only" if self.upload_scope == UPLOAD_SCOPE_OVERVIEW else "Full results"

        summary_lines = [
            f"<strong>Tech:</strong> {tech}",
            f"<strong>Ticket:</strong> {ticket}",
            f"<strong>Report Type:</strong> {report_type}",
            f"<strong>Upload Content:</strong> {scope}",
        ]
        self._job_summary.setText("<br>".join(summary_lines))

    def _update_upload_button(self):
        """Update the upload button text and style based on current state."""
        if not hasattr(self, '_upload_btn'):
            return
        has_key = is_configured()
        has_ticket = len(self._ticket_input.text().strip()) > 0
        btn_style = "border: none; border-radius: 8px; font-weight: bold; font-size: 14px;"

        if not has_key:
            self._upload_btn.setText("Configure API Key to Upload")
            self._upload_btn.setEnabled(True)
            self._upload_btn.setStyleSheet(
                f"background-color: #0C8C62; color: #FFFFFF; {btn_style}")
        elif not has_ticket:
            self._upload_btn.setText("Enter ticket number to upload")
            self._upload_btn.setEnabled(False)
            self._upload_btn.setStyleSheet(
                f"background-color: #0C8C62; color: #A8D8C0; {btn_style}")
        else:
            self._upload_btn.setText("Upload to RepairDesk")
            self._upload_btn.setEnabled(True)
            self._upload_btn.setStyleSheet(
                f"background-color: {COLORS['primary']}; color: #FFFFFF; {btn_style}")

    def _update_char_count(self):
        count = len(self._editor.toPlainText())
        self._info_label.setText(f"{count:,} characters")

    def _on_copy(self):
        html = self._extract_html()
        QApplication.clipboard().setText(html)
        self._copy_btn.setText("\u2713 Copied!")
        QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy HTML"))

    def _on_upload_clicked(self):
        if not is_configured():
            dlg = WelcomeDialog()
            dlg.exec()
            self._update_upload_button()
            return

        ticket_text = self._ticket_input.text().strip()
        if not ticket_text:
            return
        if ticket_text.upper().startswith('T-'):
            ticket_text = ticket_text[2:]
        self.ticket_id = ticket_text

        # Save tech name for next time
        self.tech_name = self._name_input.text().strip()
        if self.tech_name:
            s = load_settings()
            s['last_tech_name'] = self.tech_name
            save_settings(s)

        # Collect tech notes
        self.tech_notes = self._notes_input.toPlainText().strip()

        # Report body already contains the formatted header (report type, diagnosed by, etc.)
        # Do not prepend anything extra — the formatter owns the header
        self.edited_html = self._extract_html()
        self.accept()


# ─── Scan Summary Dialog ─────────────────────────────────────────────

class ScanSummaryDialog(QDialog):
    """
    Post-scan popup showing critical issues found.
    Shown automatically after scan completes — gives tech an immediate heads-up.
    If no issues found, shows an all-clear message.
    """

    def __init__(self, issues, specs=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan Complete — Summary")
        self.setFixedSize(480, min(120 + len(issues) * 36, 480))
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._specs = specs or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(48)
        has_issues = len(issues) > 0
        header_color = COLORS.get('error', '#EF4444') if has_issues else COLORS.get('success', '#10B981')
        header.setStyleSheet(f"background-color: {header_color};")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)
        icon = "⚠ " if has_issues else "✓ "
        title_text = f"{icon}{'Critical Issues Found' if has_issues else 'All Clear'}"
        title = QLabel(title_text)
        title.setStyleSheet(
            "color: #FFFFFF; font-size: 14px; font-weight: bold;")
        h_lay.addWidget(title)
        layout.addWidget(header)

        # ── Body ──────────────────────────────────────────────────
        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 18)
        body.setSpacing(8)

        if has_issues:
            for issue in issues:
                row = QLabel(f"• {issue}")
                row.setStyleSheet(
                    f"color: {COLORS['text_primary']}; font-size: 12px;")
                row.setWordWrap(True)
                body.addWidget(row)
        else:
            ok = QLabel("No critical issues detected. System appears healthy.")
            ok.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px;")
            ok.setWordWrap(True)
            body.addWidget(ok)

        guidance_lines = self._build_guidance_lines()
        if guidance_lines:
            body.addSpacing(8)
            guidance = QLabel("<br>".join(guidance_lines))
            guidance.setWordWrap(True)
            guidance.setOpenExternalLinks(True)
            guidance.setTextFormat(Qt.RichText)
            guidance.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 10px;")
            body.addWidget(guidance)

        body.addSpacing(8)

        # OK button
        btn = QPushButton("OK — Proceed to Upload")
        btn.setObjectName("primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(40)
        btn.clicked.connect(self.accept)
        body.addWidget(btn)

        layout.addLayout(body)

    def _build_guidance_lines(self):
        lines = []
        advanced = self._specs.get('AdvancedHealth', {}) if isinstance(self._specs, dict) else {}
        wu_health = advanced.get('windows_update', {}) if isinstance(advanced, dict) else {}
        if wu_health.get('status') == 'ok':
            driver_count = int(wu_health.get('driver_updates_count', 0) or 0)
            optional_count = int(wu_health.get('optional_updates_count', 0) or 0)
            if driver_count or optional_count:
                lines.append(
                    "Recommended next step: install the available Windows driver / optional updates, reboot if needed, then re-run the scan for updated results."
                )

        manufacturer = self._specs.get('ManufacturerUpdateTools', {}) if isinstance(self._specs, dict) else {}
        if manufacturer.get('status') == 'warning':
            vendor = manufacturer.get('vendor') or manufacturer.get('manufacturer') or 'OEM'
            url = manufacturer.get('download_url')
            label = manufacturer.get('download_label') or "manufacturer support tool"
            if url:
                lines.append(
                    f"{vendor} support app is missing. Download <a href=\"{url}\">{label}</a>, run vendor updates, then re-run the scan."
                )
            else:
                lines.append(
                    f"{vendor} support app is missing. Install the vendor update tool, run updates, then re-run the scan."
                )

        return lines



# ─── Settings Dialog ─────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Settings dialog: API key, URL, test connection, save/cancel."""

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} \u2014 Settings")
        self.setMinimumSize(760, 620)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(
                min(980, max(760, available.width() - 140)),
                min(920, max(620, available.height() - 120)),
            )
        else:
            self.resize(900, 820)

        self._settings = current_settings or load_settings()
        self.saved_settings = None  # set on save
        self._update_info = {
            'supported': False,
            'current_version': APP_VERSION,
            'latest_version': None,
            'available': False,
            'downloaded': False,
            'message': 'Update checks are idle.',
            'installer_path': None,
            'release_notes': '',
        }
        self._check_worker = None
        self._download_worker = None
        self._oauth_connected = oauth_is_connected(self._settings)

        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(60)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 16px; "
            f"font-weight: bold;")
        h_layout.addWidget(title)
        layout.addWidget(header)

        # ── Body (scrollable so Manage Techs card is always reachable) ──
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {COLORS['bg_root']}; border: none; }}"
            f"QScrollBar:vertical {{ background: {COLORS['bg_root']}; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['card_border']}; border-radius: 4px; }}"
        )

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 16, 24, 16)
        body_layout.setSpacing(12)

        # API card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        card_header = QHBoxLayout()
        card_header.setSpacing(8)

        card_title = QLabel("RepairDesk API")
        card_title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; "
            f"color: {COLORS['text_primary']}; border: none;")
        card_header.addWidget(card_title)
        card_header.addStretch()

        api_prefilled = bool(
            self._settings.get('store_name', '').strip() and (
                self._settings.get('api_key', '').strip() or self._oauth_connected
            )
        )
        self._api_toggle_btn = QPushButton()
        self._api_toggle_btn.setObjectName("secondary")
        self._api_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._api_toggle_btn.clicked.connect(self._toggle_api_card)
        card_header.addWidget(self._api_toggle_btn)
        card_layout.addLayout(card_header)

        self._api_body = QWidget()
        api_body_layout = QVBoxLayout(self._api_body)
        api_body_layout.setContentsMargins(0, 0, 0, 0)
        api_body_layout.setSpacing(8)

        # Store name row
        store_row = QHBoxLayout()
        store_row.setSpacing(8)
        store_lbl = QLabel("Store Name:")
        store_lbl.setFixedWidth(80)
        store_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        store_row.addWidget(store_lbl)

        self._store_input = QLineEdit()
        self._store_input.setText(self._settings.get('store_name', ''))
        self._store_input.setPlaceholderText("Your shop name")
        store_row.addWidget(self._store_input)
        api_body_layout.addLayout(store_row)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)
        auth_lbl = QLabel("Auth Mode:")
        auth_lbl.setFixedWidth(80)
        auth_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        auth_row.addWidget(auth_lbl)
        self._auth_mode_combo = QComboBox()
        self._auth_mode_combo.addItem("API Key", "api_key")
        self._auth_mode_combo.addItem("OAuth 2.0", "oauth")
        current_auth_mode = self._settings.get('auth_mode', 'api_key')
        self._auth_mode_combo.setCurrentIndex(1 if current_auth_mode == 'oauth' else 0)
        self._auth_mode_combo.currentIndexChanged.connect(self._sync_auth_fields)
        auth_row.addWidget(self._auth_mode_combo)
        api_body_layout.addLayout(auth_row)

        # API Key row
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_lbl = QLabel("API Key:")
        key_lbl.setFixedWidth(80)
        key_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        key_row.addWidget(key_lbl)

        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setText(self._settings.get('api_key', ''))
        key_row.addWidget(self._api_key_input)

        self._show_key_cb = QCheckBox("Show")
        self._show_key_cb.setStyleSheet(f"border: none;")
        self._show_key_cb.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._show_key_cb)
        self._api_key_row = key_row
        api_body_layout.addLayout(key_row)

        self._oauth_body = QWidget()
        oauth_body_layout = QVBoxLayout(self._oauth_body)
        oauth_body_layout.setContentsMargins(0, 0, 0, 0)
        oauth_body_layout.setSpacing(8)

        oauth_client_row = QHBoxLayout()
        oauth_client_row.setSpacing(8)
        oauth_client_lbl = QLabel("Client ID:")
        oauth_client_lbl.setFixedWidth(80)
        oauth_client_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        oauth_client_row.addWidget(oauth_client_lbl)
        self._oauth_client_id_input = QLineEdit()
        self._oauth_client_id_input.setText(self._settings.get('oauth_client_id', ''))
        oauth_client_row.addWidget(self._oauth_client_id_input)
        oauth_body_layout.addLayout(oauth_client_row)

        oauth_secret_row = QHBoxLayout()
        oauth_secret_row.setSpacing(8)
        oauth_secret_lbl = QLabel("Client Secret:")
        oauth_secret_lbl.setFixedWidth(80)
        oauth_secret_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        oauth_secret_row.addWidget(oauth_secret_lbl)
        self._oauth_client_secret_input = QLineEdit()
        self._oauth_client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._oauth_client_secret_input.setText(self._settings.get('oauth_client_secret', ''))
        oauth_secret_row.addWidget(self._oauth_client_secret_input)
        self._show_oauth_secret_cb = QCheckBox("Show")
        self._show_oauth_secret_cb.setStyleSheet("border: none;")
        self._show_oauth_secret_cb.toggled.connect(
            lambda checked: self._oauth_client_secret_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        oauth_secret_row.addWidget(self._show_oauth_secret_cb)
        oauth_body_layout.addLayout(oauth_secret_row)

        oauth_redirect_row = QHBoxLayout()
        oauth_redirect_row.setSpacing(8)
        oauth_redirect_lbl = QLabel("Redirect URI:")
        oauth_redirect_lbl.setFixedWidth(80)
        oauth_redirect_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        oauth_redirect_row.addWidget(oauth_redirect_lbl)
        self._oauth_redirect_input = QLineEdit()
        self._oauth_redirect_input.setText(
            self._settings.get('oauth_redirect_uri', DEFAULTS['oauth_redirect_uri'])
        )
        oauth_redirect_row.addWidget(self._oauth_redirect_input)
        oauth_body_layout.addLayout(oauth_redirect_row)

        oauth_actions_row = QHBoxLayout()
        oauth_actions_row.setSpacing(8)
        self._oauth_connect_btn = QPushButton("Connect OAuth")
        self._oauth_connect_btn.setObjectName("secondary")
        self._oauth_connect_btn.setCursor(Qt.PointingHandCursor)
        self._oauth_connect_btn.clicked.connect(self._connect_oauth)
        oauth_actions_row.addWidget(self._oauth_connect_btn)
        self._oauth_disconnect_btn = QPushButton("Disconnect")
        self._oauth_disconnect_btn.setObjectName("secondary")
        self._oauth_disconnect_btn.setCursor(Qt.PointingHandCursor)
        self._oauth_disconnect_btn.clicked.connect(self._disconnect_oauth)
        oauth_actions_row.addWidget(self._oauth_disconnect_btn)
        oauth_actions_row.addStretch()
        oauth_body_layout.addLayout(oauth_actions_row)

        self._oauth_status = QLabel()
        self._oauth_status.setWordWrap(True)
        self._oauth_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        oauth_body_layout.addWidget(self._oauth_status)

        api_body_layout.addWidget(self._oauth_body)
        if self._oauth_connected:
            self._oauth_status.setText("OAuth connected successfully.")
            self._oauth_status.setStyleSheet(
                f"color: {COLORS['success']}; border: none;")

        # URL row
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_lbl = QLabel("API URL:")
        url_lbl.setFixedWidth(80)
        url_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        url_row.addWidget(url_lbl)

        self._url_input = QLineEdit()
        self._url_input.setText(
            self._settings.get('api_base_url', DEFAULTS['api_base_url']))
        url_row.addWidget(self._url_input)
        api_body_layout.addLayout(url_row)

        # Test connection row
        test_row = QHBoxLayout()
        test_row.setSpacing(8)

        test_btn = QPushButton("Test Connection")
        test_btn.setObjectName("secondary")
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.clicked.connect(self._test_connection)
        test_row.addWidget(test_btn)

        self._test_status = QLabel()
        self._test_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        test_row.addWidget(self._test_status)
        test_row.addStretch()
        api_body_layout.addLayout(test_row)

        # Help text
        help_lbl = QLabel(
            "Settings are saved next to the exe on your USB.\n"
            "Use either a legacy RepairDesk API key or OAuth 2.0 desktop sign-in.")
        help_lbl.setObjectName("hint")
        help_lbl.setWordWrap(True)
        api_body_layout.addWidget(help_lbl)

        card_layout.addWidget(self._api_body)
        self._api_collapsed = api_prefilled
        self._sync_api_card_toggle()
        body_layout.addWidget(card)

        # ── WiFi Settings Card ────────────────────────────────────
        wifi_card = QFrame()
        wifi_card.setObjectName("card")
        wifi_layout = QVBoxLayout(wifi_card)
        wifi_layout.setContentsMargins(16, 12, 16, 12)
        wifi_layout.setSpacing(8)

        wifi_header = QHBoxLayout()
        wifi_header.setSpacing(8)

        wifi_title = QLabel("Auto Connect to WiFi")
        wifi_title.setStyleSheet(
            f"font-weight: bold; color: {COLORS['text_primary']}; border: none;")
        wifi_header.addWidget(wifi_title)
        wifi_header.addStretch()

        wifi_prefilled = bool(self._settings.get('wifi_ssid', '').strip())
        self._wifi_toggle_btn = QPushButton()
        self._wifi_toggle_btn.setObjectName("secondary")
        self._wifi_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._wifi_toggle_btn.clicked.connect(self._toggle_wifi_card)
        wifi_header.addWidget(self._wifi_toggle_btn)
        wifi_layout.addLayout(wifi_header)

        self._wifi_body = QWidget()
        wifi_body_layout = QVBoxLayout(self._wifi_body)
        wifi_body_layout.setContentsMargins(0, 0, 0, 0)
        wifi_body_layout.setSpacing(8)

        # SSID row
        ssid_row = QHBoxLayout()
        ssid_row.setSpacing(8)
        ssid_lbl = QLabel("SSID:")
        ssid_lbl.setFixedWidth(80)
        ssid_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; border: none;")
        ssid_row.addWidget(ssid_lbl)
        self._wifi_ssid_input = QLineEdit()
        self._wifi_ssid_input.setPlaceholderText("WiFi network name")
        self._wifi_ssid_input.setText(self._settings.get('wifi_ssid', ''))
        ssid_row.addWidget(self._wifi_ssid_input)
        wifi_body_layout.addLayout(ssid_row)

        # Password row
        pass_row = QHBoxLayout()
        pass_row.setSpacing(8)
        pass_lbl = QLabel("Password:")
        pass_lbl.setFixedWidth(80)
        pass_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; border: none;")
        pass_row.addWidget(pass_lbl)
        self._wifi_pass_input = QLineEdit()
        self._wifi_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._wifi_pass_input.setPlaceholderText("WiFi password")
        self._wifi_pass_input.setText(self._settings.get('wifi_password', ''))
        pass_row.addWidget(self._wifi_pass_input)
        self._show_wifi_cb = QCheckBox("Show")
        self._show_wifi_cb.setStyleSheet("border: none;")
        self._show_wifi_cb.toggled.connect(
            lambda checked: self._wifi_pass_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        pass_row.addWidget(self._show_wifi_cb)
        wifi_body_layout.addLayout(pass_row)

        wifi_help = QLabel("Optional. Leave blank if this shop does not want automatic WiFi connection.")
        wifi_help.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt; border: none;")
        wifi_help.setWordWrap(True)
        wifi_body_layout.addWidget(wifi_help)

        wifi_layout.addWidget(self._wifi_body)
        self._wifi_collapsed = wifi_prefilled
        self._sync_wifi_card_toggle()

        body_layout.addWidget(wifi_card)

        # ── Technicians Card ──────────────────────────────────────
        techs_card = QFrame()
        techs_card.setObjectName("card")
        techs_layout = QVBoxLayout(techs_card)
        techs_layout.setContentsMargins(16, 14, 16, 14)
        techs_layout.setSpacing(8)

        techs_header = QHBoxLayout()
        techs_header.setSpacing(8)

        techs_title = QLabel("Technicians")
        techs_title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; "
            f"color: {COLORS['text_primary']}; border: none;")
        techs_header.addWidget(techs_title)
        techs_header.addStretch()

        tech_prefilled = bool(get_technicians())
        self._techs_toggle_btn = QPushButton()
        self._techs_toggle_btn.setObjectName("secondary")
        self._techs_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._techs_toggle_btn.clicked.connect(self._toggle_techs_card)
        techs_header.addWidget(self._techs_toggle_btn)
        techs_layout.addLayout(techs_header)

        techs_hint = QLabel("Add tech names to track who ran each scan.")
        techs_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt; border: none;")
        techs_layout.addWidget(techs_hint)

        self._techs_body = QWidget()
        techs_body_layout = QVBoxLayout(self._techs_body)
        techs_body_layout.setContentsMargins(0, 0, 0, 0)
        techs_body_layout.setSpacing(8)

        _field_style = (
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 5px 10px; font-size: 10pt;"
        )
        self._tech_field_style = _field_style
        self._tech_rows_container = QWidget()
        self._tech_rows_layout = QVBoxLayout(self._tech_rows_container)
        self._tech_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._tech_rows_layout.setSpacing(6)
        self._tech_name_fields = []

        existing_names = [t.get('name', '') for t in get_technicians() if t.get('name')]
        for name in existing_names or [""]:
            self._add_tech_row(name)

        techs_body_layout.addWidget(self._tech_rows_container)

        add_tech_btn = QPushButton("+ Add Technician")
        add_tech_btn.setObjectName("secondary")
        add_tech_btn.setCursor(Qt.PointingHandCursor)
        add_tech_btn.clicked.connect(lambda: self._add_tech_row(""))
        techs_body_layout.addWidget(add_tech_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        techs_layout.addWidget(self._techs_body)
        self._techs_collapsed = tech_prefilled
        self._sync_techs_card_toggle()

        body_layout.addWidget(techs_card)

        # ── App Updates Card ─────────────────────────────────────
        updates_card = QWidget()
        updates_card.setStyleSheet(
            f"background-color: {COLORS['card_bg']}; "
            f"border: 1px solid {COLORS['card_border']}; border-radius: 12px;")
        updates_layout = QVBoxLayout(updates_card)
        updates_layout.setContentsMargins(16, 14, 16, 14)
        updates_layout.setSpacing(8)

        updates_title = QLabel("App Updates")
        updates_title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; "
            f"color: {COLORS['text_primary']}; border: none;")
        updates_layout.addWidget(updates_title)

        version_row = QHBoxLayout()
        version_row.setSpacing(8)
        version_lbl = QLabel("Current Version:")
        version_lbl.setFixedWidth(110)
        version_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        version_row.addWidget(version_lbl)
        self._update_current_version = QLabel(APP_VERSION)
        self._update_current_version.setStyleSheet(
            f"color: {COLORS['text_primary']}; border: none;")
        version_row.addWidget(self._update_current_version)
        version_row.addStretch()
        updates_layout.addLayout(version_row)

        latest_row = QHBoxLayout()
        latest_row.setSpacing(8)
        latest_lbl = QLabel("Latest Version:")
        latest_lbl.setFixedWidth(110)
        latest_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        latest_row.addWidget(latest_lbl)
        self._update_latest_version = QLabel("Not checked yet")
        self._update_latest_version.setStyleSheet(
            f"color: {COLORS['text_primary']}; border: none;")
        latest_row.addWidget(self._update_latest_version)
        latest_row.addStretch()
        updates_layout.addLayout(latest_row)

        self._include_beta_updates_cb = QCheckBox("Include beta builds")
        self._include_beta_updates_cb.setChecked(
            bool(self._settings.get('include_beta_updates', False))
        )
        self._include_beta_updates_cb.setStyleSheet(
            f"color: {COLORS['text_primary']}; border: none;")
        updates_layout.addWidget(self._include_beta_updates_cb)

        beta_hint = QLabel(
            "Enable this to receive beta/prerelease builds in addition to normal stable releases."
        )
        beta_hint.setWordWrap(True)
        beta_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt; border: none;")
        updates_layout.addWidget(beta_hint)

        self._update_status = QLabel("Update checks are idle.")
        self._update_status.setWordWrap(True)
        self._update_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; border: none;")
        updates_layout.addWidget(self._update_status)

        self._update_progress = RoundedProgressWidget()
        self._update_progress.setRange(0, 100)
        self._update_progress.setValue(0)
        self._update_progress.setTextVisible(True)
        self._update_progress.setFormat("%p%")
        self._update_progress.setFixedHeight(24)
        self._update_progress.hide()
        updates_layout.addWidget(self._update_progress)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)

        self._check_updates_btn = QPushButton("Check for Updates")
        self._check_updates_btn.setObjectName("primary")
        self._check_updates_btn.setCursor(Qt.PointingHandCursor)
        self._check_updates_btn.clicked.connect(self._on_check_updates)
        btns_row.addWidget(self._check_updates_btn)

        self._download_update_btn = QPushButton("Download Update")
        self._download_update_btn.setObjectName("secondary")
        self._download_update_btn.setCursor(Qt.PointingHandCursor)
        self._download_update_btn.setEnabled(False)
        self._download_update_btn.clicked.connect(self._on_download_update)
        btns_row.addWidget(self._download_update_btn)

        self._install_update_btn = QPushButton("Install Update Now")
        self._install_update_btn.setObjectName("primary")
        self._install_update_btn.setCursor(Qt.PointingHandCursor)
        self._install_update_btn.setEnabled(False)
        self._install_update_btn.clicked.connect(self._on_install_update)
        btns_row.addWidget(self._install_update_btn)

        updates_layout.addLayout(btns_row)

        readme_row = QHBoxLayout()
        readme_row.setSpacing(8)

        self._readme_btn = QPushButton("Open Read Me")
        self._readme_btn.setObjectName("secondary")
        self._readme_btn.setCursor(Qt.PointingHandCursor)
        self._readme_btn.clicked.connect(self._open_readme)
        readme_row.addWidget(self._readme_btn, 0)

        readme_hint = QLabel(
            "Open the local user guide with scan instructions, button explanations, and test details."
        )
        readme_hint.setWordWrap(True)
        readme_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt; border: none;")
        readme_row.addWidget(readme_hint, 1)

        updates_layout.addLayout(readme_row)

        feedback_row = QHBoxLayout()
        feedback_row.setSpacing(8)

        self._feedback_btn = QPushButton("Feature Request / Report Bug")
        self._feedback_btn.setObjectName("secondary")
        self._feedback_btn.setCursor(Qt.PointingHandCursor)
        self._feedback_btn.clicked.connect(self._on_feedback_email)
        feedback_row.addWidget(self._feedback_btn, 0)

        feedback_hint = QLabel(
            "Opens your email app with version details and the latest log path."
        )
        feedback_hint.setWordWrap(True)
        feedback_hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 9pt; border: none;")
        feedback_row.addWidget(feedback_hint, 1)

        updates_layout.addLayout(feedback_row)
        body_layout.addWidget(updates_card)

        body_layout.addStretch()

        # Version label
        footer_version_lbl = QLabel(f"Version {APP_VERSION}")
        footer_version_lbl.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 9pt; border: none;")
        footer_version_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_layout.addWidget(footer_version_lbl)

        scroll_area.setWidget(body)
        layout.addWidget(scroll_area, 1)

        # ── Button bar ────────────────────────────────────────────
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(24, 0, 24, 20)
        btn_bar.setSpacing(8)
        btn_bar.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_bar.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_bar.addWidget(save_btn)

        layout.addLayout(btn_bar)
        self._load_pending_update_state()
        self._sync_auth_fields()

    def _toggle_key_visibility(self, checked):
        self._api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked
            else QLineEdit.EchoMode.Password)

    def _sync_auth_fields(self):
        use_oauth = self._auth_mode_combo.currentData() == 'oauth'
        key_label_item = self._api_key_row.itemAt(0)
        if key_label_item and key_label_item.widget():
            key_label_item.widget().setVisible(not use_oauth)
        self._api_key_input.setVisible(not use_oauth)
        self._show_key_cb.setVisible(not use_oauth)
        self._oauth_body.setVisible(use_oauth)

    def _sync_api_card_toggle(self):
        self._api_body.setVisible(not self._api_collapsed)
        self._api_toggle_btn.setText(
            "Show Details" if self._api_collapsed else "Hide Details"
        )

    def _toggle_api_card(self):
        self._api_collapsed = not self._api_collapsed
        self._sync_api_card_toggle()

    def _get_latest_log_path(self):
        log_dir = Path(get_app_dir()) / "logs"
        if not log_dir.is_dir():
            return None
        candidates = sorted(
            log_dir.glob("AutoSpecUploader_*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _build_feedback_email(self):
        latest_log = self._get_latest_log_path()
        channel = "Beta" if self._include_beta_updates_cb.isChecked() else "Stable"
        machine_name = os.environ.get("COMPUTERNAME") or platform.node() or "Unknown"

        subject = f"PC AutoSpec Feedback - v{APP_VERSION}"
        body_lines = [
            "Please describe the bug or feature request here:",
            "",
            "What happened:",
            "",
            "What you expected:",
            "",
            "Steps to reproduce (if reporting a bug):",
            "",
            "---",
            f"App version: {APP_VERSION}",
            f"Update channel selected: {channel}",
            f"Machine name: {machine_name}",
            f"Platform: {platform.system()} {platform.release()}",
            "",
            "Please attach the latest log file if this is a bug report.",
            f"Latest log path: {latest_log if latest_log else 'No log file found yet'}",
            "",
            "If your email app does not attach files automatically, attach the log manually from the path above.",
        ]
        return subject, "\n".join(body_lines), latest_log

    def _on_feedback_email(self):
        subject, body, latest_log = self._build_feedback_email()
        mailto = f"mailto:?subject={quote(subject)}&body={quote(body)}"

        if QDesktopServices.openUrl(QUrl(mailto)):
            return

        fallback = _make_msgbox(
            self,
            "Email App Not Available",
            "PC AutoSpec could not open your default email app.\n\n"
            "A support template has been copied to your clipboard.\n"
            + (f"\nLatest log: {latest_log}" if latest_log else ""),
            QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        )
        QApplication.clipboard().setText(f"Subject: {subject}\n\n{body}")
        fallback.exec()

    def _get_readme_path(self):
        candidates = [
            Path(get_app_dir()) / README_FILENAME,
            Path(get_app_dir()) / "README.md",
        ]
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.extend([
                exe_dir / README_FILENAME,
                exe_dir / "README.md",
            ])
            if hasattr(sys, '_MEIPASS'):
                meipass_dir = Path(sys._MEIPASS)
                candidates.extend([
                    meipass_dir / README_FILENAME,
                    meipass_dir / "README.md",
                ])
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _open_readme(self):
        readme_path = self._get_readme_path()
        if readme_path and QDesktopServices.openUrl(QUrl.fromLocalFile(str(readme_path))):
            return

        fallback = _make_msgbox(
            self,
            "Read Me Not Found",
            "PC AutoSpec could not find the local Read Me file in the app folder.",
            QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        )
        fallback.exec()

    def _sync_wifi_card_toggle(self):
        self._wifi_body.setVisible(not self._wifi_collapsed)
        self._wifi_toggle_btn.setText(
            "Show Details" if self._wifi_collapsed else "Hide Details"
        )

    def _toggle_wifi_card(self):
        self._wifi_collapsed = not self._wifi_collapsed
        self._sync_wifi_card_toggle()

    def _sync_techs_card_toggle(self):
        self._techs_body.setVisible(not self._techs_collapsed)
        self._techs_toggle_btn.setText(
            "Show Details" if self._techs_collapsed else "Hide Details"
        )

    def _toggle_techs_card(self):
        self._techs_collapsed = not self._techs_collapsed
        self._sync_techs_card_toggle()

    def _add_tech_row(self, name=""):
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        field = QLineEdit()
        field.setPlaceholderText("Technician name")
        field.setFixedHeight(32)
        field.setStyleSheet(self._tech_field_style)
        field.setText(name)
        row.addWidget(field)

        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("secondary")
        remove_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(remove_btn)

        self._tech_rows_layout.addWidget(row_widget)
        self._tech_name_fields.append(field)

        def _remove_row():
            self._tech_name_fields = [f for f in self._tech_name_fields if f is not field]
            row_widget.setParent(None)
            row_widget.deleteLater()
            if not self._tech_name_fields:
                self._add_tech_row("")

        remove_btn.clicked.connect(_remove_row)
        return field

    def _test_connection(self):
        self._test_status.setText("Testing...")
        self._test_status.setStyleSheet(
            f"color: {COLORS['warning']}; border: none;")
        QApplication.processEvents()

        url = self._url_input.text().strip()
        auth_mode = self._auth_mode_combo.currentData()
        if auth_mode == 'oauth' and not self._oauth_connected:
            self._test_status.setText("Connect OAuth first")
            self._test_status.setStyleSheet(
                f"color: {COLORS['error']}; border: none;")
            return
        key = self._api_key_input.text().strip()
        if auth_mode != 'oauth' and not key:
            self._test_status.setText("No API key entered")
            self._test_status.setStyleSheet(
                f"color: {COLORS['error']}; border: none;")
            return

        api = RepairDeskAPI(api_key=key, base_url=url, auth_mode=auth_mode)
        ok, msg = api.test_connection()
        color = COLORS['success'] if ok else COLORS['error']
        self._test_status.setText(msg)
        self._test_status.setStyleSheet(
            f"color: {color}; border: none;")

    def _connect_oauth(self):
        oauth_settings = dict(self._settings)
        oauth_settings['auth_mode'] = 'oauth'
        oauth_settings['oauth_client_id'] = self._oauth_client_id_input.text().strip()
        oauth_settings['oauth_client_secret'] = self._oauth_client_secret_input.text().strip()
        oauth_settings['oauth_redirect_uri'] = (
            self._oauth_redirect_input.text().strip() or DEFAULTS['oauth_redirect_uri']
        )
        try:
            self._oauth_status.setText("Opening RepairDesk sign-in...")
            self._oauth_status.setStyleSheet(
                f"color: {COLORS['warning']}; border: none;")
            QApplication.processEvents()
            run_oauth_flow(oauth_settings)
            self._oauth_connected = True
            self._settings = load_settings()
            self._oauth_status.setText("OAuth connected successfully.")
            self._oauth_status.setStyleSheet(
                f"color: {COLORS['success']}; border: none;")
        except Exception as e:
            self._oauth_connected = False
            self._oauth_status.setText(str(e))
            self._oauth_status.setStyleSheet(
                f"color: {COLORS['error']}; border: none;")

    def _disconnect_oauth(self):
        settings = load_settings()
        clear_oauth_tokens(settings)
        self._oauth_connected = False
        self._oauth_status.setText("OAuth connection cleared.")
        self._oauth_status.setStyleSheet(
            f"color: {COLORS['warning']}; border: none;")

    def _set_update_status(self, message, color=None):
        color = color or COLORS['text_secondary']
        self._update_status.setText(message)
        self._update_status.setStyleSheet(
            f"color: {color}; border: none;")

    def _set_update_button_style(self, button, enabled, primary=False):
        if enabled:
            if primary:
                button.setStyleSheet(
                    f"background-color: {COLORS['primary']}; color: white; "
                    "border: none; border-radius: 8px; font-weight: bold;")
            else:
                button.setStyleSheet(
                    f"background-color: {COLORS['card_bg']}; "
                    f"color: {COLORS['text_primary']}; "
                    f"border: 1px solid {COLORS['card_border']}; "
                    "border-radius: 8px; font-weight: bold;")
        else:
            button.setStyleSheet(
                "background-color: #444444; color: #888888; "
                "border: none; border-radius: 8px; font-weight: bold;")

    def _refresh_update_buttons(self):
        supported = self._update_info.get('supported', False)
        available = self._update_info.get('available', False)
        downloaded = self._update_info.get('downloaded', False)
        has_download_url = bool(self._update_info.get('download_url'))

        busy = self._check_worker is not None or self._download_worker is not None
        install_enabled = (
            supported and downloaded and bool(self._update_info.get('installer_path')) and not busy
        )
        download_enabled = (
            supported and available and has_download_url and not downloaded and not busy
        )
        check_enabled = not busy and not available and not downloaded

        self._check_updates_btn.setEnabled(check_enabled)
        self._download_update_btn.setEnabled(download_enabled)
        self._install_update_btn.setEnabled(install_enabled)

        self._set_update_button_style(self._check_updates_btn, check_enabled, primary=check_enabled)
        self._set_update_button_style(self._download_update_btn, download_enabled, primary=download_enabled)
        self._set_update_button_style(self._install_update_btn, install_enabled, primary=True)

    def _load_pending_update_state(self):
        pending = get_pending_update()
        if pending:
            self._update_info.update({
                'supported': True,
                'available': True,
                'downloaded': True,
                'package_path': pending.get('package_path') or pending.get('installer_path'),
                'latest_version': pending.get('version'),
                'installer_path': pending.get('installer_path'),
                'package_kind': pending.get('package_kind', 'installer'),
                'message': (
                    f"Update {pending.get('version')} has already been downloaded. "
                    "Use Install Update Now to apply it."
                ),
            })
            self._update_latest_version.setText(str(pending.get('version', 'Unknown')))
            self._set_update_status(self._update_info['message'], COLORS['success'])
            self._update_progress.show()
            self._update_progress.setValue(100)
        self._refresh_update_buttons()

    def _on_check_updates(self):
        self._update_progress.hide()
        self._update_progress.setValue(0)
        self._set_update_status("Checking for updates...", COLORS['warning'])
        self._update_latest_version.setText("Checking...")
        self._check_updates_btn.setEnabled(False)

        self._check_worker = UpdateCheckWorker(
            include_prereleases=self._include_beta_updates_cb.isChecked(),
            parent=self,
        )
        self._check_worker.finished.connect(self._on_update_check_finished)
        self._check_worker.error.connect(self._on_update_check_error)
        self._check_worker.start()

    def _on_update_check_finished(self, info):
        self._check_worker = None
        self._update_info = dict(info)
        latest_version = info.get('latest_version') or "Not available"
        self._update_latest_version.setText(str(latest_version))

        if info.get('downloaded'):
            self._update_progress.show()
            self._update_progress.setValue(100)
            self._set_update_status(info.get('message', ''), COLORS['success'])
        elif info.get('available'):
            self._update_progress.hide()
            self._set_update_status(info.get('message', ''), COLORS['warning'])
        elif info.get('supported'):
            self._update_progress.hide()
            self._set_update_status(info.get('message', ''), COLORS['success'])
        else:
            self._update_progress.hide()
            self._set_update_status(info.get('message', ''), COLORS['warning'])

        self._refresh_update_buttons()

    def _on_update_check_error(self, message):
        self._check_worker = None
        self._update_progress.hide()
        self._set_update_status(f"Update check failed: {message}", COLORS['error'])
        self._refresh_update_buttons()

    def _on_download_update(self):
        if not self._update_info.get('available'):
            return

        self._update_progress.show()
        self._update_progress.setValue(0)
        self._set_update_status("Preparing update download...", COLORS['warning'])
        self._refresh_update_buttons()

        self._download_worker = UpdateDownloadWorker(dict(self._update_info), self)
        self._download_worker.progress.connect(self._on_update_download_progress)
        self._download_worker.finished.connect(self._on_update_download_finished)
        self._download_worker.error.connect(self._on_update_download_error)
        self._download_worker.start()

    def _on_update_download_progress(self, percent, message):
        self._update_progress.show()
        self._update_progress.setValue(percent)
        self._set_update_status(message, COLORS['warning'])

    def _on_update_download_finished(self, result):
        self._download_worker = None
        self._update_info.update({
            'supported': True,
            'available': True,
            'downloaded': True,
            'package_path': result.get('package_path') or result.get('installer_path'),
            'installer_path': result.get('installer_path'),
            'package_kind': result.get('package_kind') or self._update_info.get('package_kind'),
            'latest_version': result.get('version') or self._update_info.get('latest_version'),
            'message': result.get('message', 'Update downloaded.'),
        })
        self._update_latest_version.setText(str(self._update_info.get('latest_version') or "Unknown"))
        self._update_progress.show()
        self._update_progress.setValue(100)
        self._set_update_status(self._update_info['message'], COLORS['success'])
        self._refresh_update_buttons()

    def _on_update_download_error(self, message):
        self._download_worker = None
        self._update_progress.hide()
        self._set_update_status(f"Update download failed: {message}", COLORS['error'])
        self._refresh_update_buttons()

    def _on_install_update(self):
        package_path = self._update_info.get('package_path') or self._update_info.get('installer_path')
        package_kind = str(self._update_info.get('package_kind') or 'installer')
        if not package_path:
            return

        if package_kind == 'portable':
            prompt = (
                "The downloaded portable update will replace the app files in the folder "
                "PC AutoSpec is currently running from after the app closes.\n\n"
                "It will not register PC AutoSpec as an installed app on this machine.\n\n"
                "PC AutoSpec should reopen automatically when the portable update finishes.\n\n"
                "Save any work first, then continue."
            )
        else:
            prompt = (
                "The downloaded installer will open after PC AutoSpec closes.\n\n"
                "It will prefill the folder PC AutoSpec is currently running from,\n"
                "so USB updates stay on the USB by default.\n\n"
                "Save any work first, then continue."
            )
        box = _make_msgbox(
            self,
            "Install Update",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if box.exec() != int(QMessageBox.StandardButton.Yes):
            return

        try:
            launch_pending_update(package_path)
        except Exception as e:
            self._set_update_status(f"Could not launch update: {e}", COLORS['error'])
            return

        self.accept()
        app = QApplication.instance()
        if app:
            app.setProperty("pcautospec_update_in_progress", True)
            app.quit()

    def _on_save(self):
        wifi_ssid = self._wifi_ssid_input.text().strip()
        wifi_password = self._wifi_pass_input.text().strip()
        self.saved_settings = {
            'store_name': self._store_input.text().strip(),
            'auth_mode': self._auth_mode_combo.currentData(),
            'api_key': self._api_key_input.text().strip(),
            'api_base_url': (self._url_input.text().strip()
                             or DEFAULTS['api_base_url']),
            'oauth_authorize_url': self._settings.get('oauth_authorize_url', DEFAULTS['oauth_authorize_url']),
            'oauth_token_url': self._settings.get('oauth_token_url', DEFAULTS['oauth_token_url']),
            'oauth_client_id': self._oauth_client_id_input.text().strip(),
            'oauth_client_secret': self._oauth_client_secret_input.text().strip(),
            'oauth_redirect_uri': (
                self._oauth_redirect_input.text().strip() or DEFAULTS['oauth_redirect_uri']
            ),
            'oauth_access_token': self._settings.get('oauth_access_token', ''),
            'oauth_refresh_token': self._settings.get('oauth_refresh_token', ''),
            'oauth_token_expires_at': self._settings.get('oauth_token_expires_at', ''),
            'tickets_per_page': self._settings.get(
                'tickets_per_page', DEFAULTS['tickets_per_page']),
            'include_beta_updates': self._include_beta_updates_cb.isChecked(),
            'wifi_ssid': wifi_ssid,
            'wifi_password': wifi_password,
            'wifi_auto_connect': bool(wifi_ssid),
            # Save technician names from inline fields (empty fields ignored)
            'technicians': [{'name': f.text().strip()}
                            for f in self._tech_name_fields if f.text().strip()],
            'last_tech_name': self._settings.get('last_tech_name', ''),
        }
        save_settings(self.saved_settings)
        logging.info("Settings saved and applied")
        self.accept()


# ─── CPU Stress Test Progress Dialog ────────────────────────────────

class StressTestDialog(QDialog):
    cancel_requested = Signal()
    """
    Modal dialog shown during the CPU load temperature test.

    - Shows ramp phase (fans spinning up) then measurement phase
    - Displays a countdown progress bar
    - Shows live temperature samples as they arrive
    - Auto-closes when the test finishes
    - Cannot be closed manually during the test
    """

    def __init__(self, duration_sec: int = 20, ramp_sec: int = 10, parent=None):
        super().__init__(parent)
        self._duration = duration_sec
        self._ramp = ramp_sec
        self._total = ramp_sec + duration_sec
        self._elapsed = 0
        self._finished = False
        self._in_ramp = True

        self.setWindowTitle("CPU Stress Test")
        self.setFixedSize(420, 300)
        self.setModal(True)
        self.setWindowFlags(
            Qt.Dialog |
            Qt.CustomizeWindowHint |
            Qt.WindowTitleHint
        )
        self.setStyleSheet(f"background-color: {COLORS['bg_root']};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet(f"background-color: {COLORS['header_bg']};")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)
        self._header_lbl = QLabel("🌀 Ramping Up — Fans Spinning Up")
        self._header_lbl.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 13px; font-weight: bold;")
        h_lay.addWidget(self._header_lbl)
        outer.addWidget(header)

        # ── Body ──────────────────────────────────────────────────
        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(14)

        self._info_lbl = QLabel(
            f"Gradually increasing CPU load over {ramp_sec}s so fans can spin up, "
            f"then measuring peak temperature for {duration_sec}s. Please wait..."
        )
        self._info_lbl.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px;")
        self._info_lbl.setWordWrap(True)
        body.addWidget(self._info_lbl)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, self._total)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(10)
        self._update_progress_style(ramp=True)
        body.addWidget(self._progress)

        # Phase + countdown label
        self._countdown_lbl = QLabel(f"Ramp: {ramp_sec}s remaining")
        self._countdown_lbl.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px;")
        body.addWidget(self._countdown_lbl)

        # Live temp label
        self._temp_lbl = QLabel("CPU Temperature: reading…")
        self._temp_lbl.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: bold;")
        body.addWidget(self._temp_lbl)

        # Warning note
        note = QLabel("⚠ Test will abort automatically if temperature exceeds 100°C")
        note.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px;")
        note.setWordWrap(True)
        body.addWidget(note)

        body.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel Stress Test")
        self._cancel_btn.setObjectName("secondary")
        self._cancel_btn.setFixedHeight(38)
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)

        body.addLayout(btn_row)
        outer.addLayout(body)

        # ── Tick timer ────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _update_progress_style(self, ramp: bool):
        color = "#F59E0B" if ramp else COLORS.get('accent', '#10B981')
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {COLORS['card_bg']};
                border-radius: 5px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 5px;
            }}
        """)

    def _tick(self):
        """Advance the progress bar each second."""
        self._elapsed += 1
        self._progress.setValue(self._elapsed)

        if self._elapsed <= self._ramp:
            remaining = self._ramp - self._elapsed
            self._countdown_lbl.setText(f"Ramp phase: {remaining}s remaining")
        else:
            if self._in_ramp:
                # Transition to measurement phase
                self._in_ramp = False
                self._header_lbl.setText("🔥 Measuring Peak Temperature")
                self._update_progress_style(ramp=False)
            remaining = max(0, self._total - self._elapsed)
            self._countdown_lbl.setText(f"Measuring: {remaining}s remaining")

        if self._elapsed >= self._total:
            self._timer.stop()

    def update_temp(self, temp_c: float):
        """Called by the worker each time a new temp sample arrives."""
        if temp_c < 75:
            color = COLORS['success']
        elif temp_c < 90:
            color = COLORS['warning']
        else:
            color = COLORS['error']
        self._temp_lbl.setText(f"CPU Temperature: {temp_c:.0f}°C")
        self._temp_lbl.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: bold;")

    def finish(self):
        """Call when the stress test is complete — closes the dialog."""
        self._finished = True
        self._timer.stop()
        self.accept()

    def mark_cancelling(self):
        """Lock the dialog into a waiting state while the worker unwinds."""
        self._cancel_btn.setEnabled(False)
        self._header_lbl.setText("Stopping CPU Stress Test...")
        self._countdown_lbl.setText("Cancelling test and letting the scan continue...")
        self._info_lbl.setText(
            "The CPU load is being stopped now. This can take a moment while the worker process exits cleanly."
        )

    def _on_cancel_clicked(self):
        self.mark_cancelling()
        self.cancel_requested.emit()

    def closeEvent(self, event):
        """Prevent manual close during test."""
        if self._finished:
            event.accept()
        else:
            event.ignore()


# =============================================================================
# ManageTechsDialog — Add / remove technicians and their API keys
# =============================================================================

class ManageTechsDialog(QDialog):
    """
    Dialog for managing the technician roster.
    Each tech has a display name and a RepairDesk API key.
    The list is persisted to settings.json on the USB stick.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Technicians")
        self.setMinimumWidth(540)
        self.setMinimumHeight(420)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']}; color: {COLORS['text_primary']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ── Header ────────────────────────────────────────────────
        hdr = QLabel("Technician Roster")
        hdr.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13pt; font-weight: bold;"
        )
        layout.addWidget(hdr)

        sub = QLabel(
            "Add technician names to track who ran each scan. "
            "RepairDesk uses a single store API key — notes post under your store account regardless."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 9pt;")
        layout.addWidget(sub)

        # ── Tech list (scrollable) ────────────────────────────────
        self._list_widget = QListWidget()
        self._list_widget.setStyleSheet(
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; border-radius: 6px; "
            "font-size: 10pt;"
        )
        self._list_widget.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self._list_widget, 1)

        # ── Add / Remove buttons ──────────────────────────────────
        btn_row = QHBoxLayout()
        btn_style = (
            f"background-color: {COLORS['button_bg']}; "
            f"color: {COLORS['button_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 6px 16px; font-size: 9pt;"
        )
        self._add_btn = QPushButton("+ Add Technician")
        self._add_btn.setStyleSheet(btn_style)
        self._add_btn.clicked.connect(self._add_tech)
        btn_row.addWidget(self._add_btn)

        self._edit_btn = QPushButton("✏ Edit")
        self._edit_btn.setStyleSheet(btn_style)
        self._edit_btn.clicked.connect(self._edit_tech)
        btn_row.addWidget(self._edit_btn)

        self._remove_btn = QPushButton("🗑 Remove")
        self._remove_btn.setStyleSheet(
            f"background-color: {COLORS['button_bg']}; "
            f"color: {COLORS['error']}; "
            f"border: 1px solid {COLORS['error']}; "
            "border-radius: 6px; padding: 6px 16px; font-size: 9pt;"
        )
        self._remove_btn.clicked.connect(self._remove_tech)
        btn_row.addWidget(self._remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Close ─────────────────────────────────────────────────
        close_btn = QPushButton("Done")
        close_btn.setFixedHeight(36)
        close_btn.setStyleSheet(
            f"background-color: {COLORS['success']}; color: #fff; "
            "border: none; border-radius: 6px; font-size: 10pt; font-weight: bold;"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._load_list()

    # ── Internal helpers ──────────────────────────────────────────

    def _load_list(self):
        self._list_widget.clear()
        for tech in get_technicians():
            name = tech.get('name', '')
            item = QListWidgetItem(f"  {name}")
            item.setData(Qt.UserRole, tech)
            self._list_widget.addItem(item)

    def _add_tech(self):
        dlg = _TechEditDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            techs = get_technicians()
            # Prevent duplicate names
            if any(t['name'] == dlg.tech_name for t in techs):
                box = _make_msgbox(self, "Duplicate Name",
                                   f"A technician named '{dlg.tech_name}' already exists.")
                box.exec()
                return
            techs.append({'name': dlg.tech_name})
            save_technicians(techs)
            self._load_list()

    def _edit_tech(self):
        item = self._list_widget.currentItem()
        if not item:
            return
        tech = item.data(Qt.UserRole)
        dlg = _TechEditDialog(parent=self, prefill_name=tech.get('name', ''))
        if dlg.exec() == QDialog.Accepted:
            techs = get_technicians()
            for t in techs:
                if t['name'] == tech['name']:
                    t['name'] = dlg.tech_name
                    break
            save_technicians(techs)
            self._load_list()

    def _remove_tech(self):
        item = self._list_widget.currentItem()
        if not item:
            return
        tech = item.data(Qt.UserRole)
        name = tech.get('name', '')
        box = _make_msgbox(self, "Remove Technician",
                           f"Remove '{name}' from the roster?",
                           buttons=QMessageBox.Yes | QMessageBox.No,
                           default=QMessageBox.No)
        if box.exec() == QMessageBox.StandardButton.Yes.value:
            techs = [t for t in get_technicians() if t['name'] != name]
            save_technicians(techs)
            self._load_list()


# =============================================================================
# _TechEditDialog — small inline dialog for name + API key entry
# =============================================================================

class _TechEditDialog(QDialog):
    def __init__(self, parent=None, prefill_name=''):
        super().__init__(parent)
        self.setWindowTitle("Add / Edit Technician")
        self.setFixedSize(380, 180)
        self.setStyleSheet(f"background-color: {COLORS['bg_root']}; color: {COLORS['text_primary']};")

        self.tech_name = ''

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        input_style = (
            f"background-color: {COLORS['console_bg']}; "
            f"color: {COLORS['console_text']}; "
            f"border: 1px solid {COLORS['card_border']}; "
            "border-radius: 6px; padding: 6px 10px; font-size: 10pt;"
        )
        lbl_style = f"color: {COLORS['text_secondary']}; font-size: 9pt; font-weight: bold;"

        layout.addWidget(_lbl := QLabel("Technician Name"))
        _lbl.setStyleSheet(lbl_style)
        self._name_edit = QLineEdit(prefill_name)
        self._name_edit.setPlaceholderText("e.g. Jake")
        self._name_edit.setFixedHeight(36)
        self._name_edit.setStyleSheet(input_style)
        layout.addWidget(self._name_edit)

        layout.addStretch()

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setStyleSheet(
            f"background-color: {COLORS['button_bg']}; color: {COLORS['text_secondary']}; "
            f"border: 1px solid {COLORS['card_border']}; border-radius: 6px; font-size: 9pt;"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(34)
        save_btn.setStyleSheet(
            f"background-color: {COLORS['success']}; color: #fff; "
            "border: none; border-radius: 6px; font-size: 10pt; font-weight: bold;"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            box = _make_msgbox(self, "Missing Name", "Please enter a technician name.")
            box.exec()
            return
        self.tech_name = name
        self.api_key = key
        self.accept()
