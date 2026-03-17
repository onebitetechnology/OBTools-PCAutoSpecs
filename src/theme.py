"""
PC AutoSpec — Theme and QSS stylesheet.
Dark theme with RepairDesk green accent.
"""

COLORS = {
    # Header
    'header_bg': '#1A1A1A',
    'header_text': '#FFFFFF',

    # Primary action (green)
    'primary': '#10B981',
    'primary_hover': '#059669',
    'primary_active': '#047857',
    'primary_light': '#34D399',

    # Backgrounds (darkest → lightest)
    'bg_root': '#111827',
    'bg_panel': '#1A1F2E',
    'bg_dark': '#0D1117',
    'card_bg': '#232B3B',
    'card_border': '#374151',
    'row_alt': '#272F40',

    # Text
    'text_primary': '#E5E7EB',
    'text_secondary': '#9CA3AF',
    'text_tertiary': '#6B7280',
    'text_white': '#FFFFFF',

    # Console / Activity log
    'console_bg': '#1F2937',
    'console_text': '#E5E7EB',
    'console_success': '#34D399',
    'console_warning': '#FBBF24',
    'console_error': '#F87171',
    'console_info': '#60A5FA',

    # Status colors
    'success': '#10B981',
    'warning': '#F59E0B',
    'error': '#EF4444',
    'info': '#3B82F6',

    # Borders
    'border': '#2D3748',
    'border_light': '#374151',

    # Status bar
    'status_bg': '#1E2736',
}


def build_stylesheet() -> str:
    """Build the global QSS stylesheet for the application."""
    c = COLORS
    return f"""
    /* ── Global ─────────────────────────── */
    QMainWindow {{
        background-color: {c['bg_root']};
    }}
    QWidget {{
        font-family: 'Segoe UI', sans-serif;
    }}

    /* ── Rounded cards ──────────────────── */
    QFrame#card {{
        background-color: {c['card_bg']};
        border: 1px solid {c['card_border']};
        border-radius: 12px;
    }}

    /* ── Header bar ─────────────────────── */
    QFrame#header {{
        background-color: {c['header_bg']};
        border: none;
        border-radius: 0px;
    }}

    /* ── Notification bar ───────────────── */
    QFrame#notificationBar {{
        background-color: {c['primary']};
        border-radius: 0px;
    }}

    /* ── Primary button (green) ────────── */
    QPushButton#primary {{
        background-color: {c['primary']};
        color: {c['text_white']};
        border: none;
        border-radius: 8px;
        padding: 10px 28px;
        font-weight: bold;
        font-size: 13px;
    }}
    QPushButton#primary:hover {{
        background-color: {c['primary_hover']};
    }}
    QPushButton#primary:pressed {{
        background-color: {c['primary_active']};
    }}
    QPushButton#primary:disabled {{
        background-color: #0C8C62;
        color: #A8D8C0;
    }}

    /* ── Secondary button ───────────────── */
    QPushButton#secondary {{
        background-color: {c['card_bg']};
        color: {c['text_primary']};
        border: 1px solid {c['card_border']};
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 12px;
    }}
    QPushButton#secondary:hover {{
        background-color: {c['border_light']};
    }}

    /* ── Flat/icon button ───────────────── */
    QPushButton#flat {{
        background-color: transparent;
        border: none;
        color: {c['text_tertiary']};
        font-size: 16px;
        padding: 4px 8px;
    }}
    QPushButton#flat:hover {{
        color: {c['text_primary']};
        background-color: rgba(255, 255, 255, 0.05);
        border-radius: 4px;
    }}

    /* ── Input fields ───────────────────── */
    QLineEdit {{
        background-color: {c['console_bg']};
        color: {c['text_primary']};
        border: 2px solid {c['border_light']};
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
        selection-background-color: {c['primary']};
    }}
    QLineEdit:focus {{
        border-color: {c['primary']};
    }}
    QLineEdit:disabled {{
        color: {c['text_tertiary']};
    }}

    /* ── Check boxes ────────────────────── */
    QCheckBox {{
        color: {c['text_secondary']};
        font-size: 12px;
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {c['border']};
        border-radius: 3px;
        background-color: {c['bg_panel']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {c['primary']};
        border-color: {c['primary']};
    }}

    /* ── Scrollbars ─────────────────────── */
    QScrollBar:vertical {{
        background: {c['bg_panel']};
        width: 10px;
        margin: 0;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        min-height: 30px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['text_tertiary']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        height: 0px;
    }}

    /* ── Activity log ───────────────────── */
    QTextEdit#activityLog {{
        background-color: {c['console_bg']};
        color: {c['console_text']};
        border: none;
        border-radius: 8px;
        padding: 8px;
        font-family: 'Consolas', monospace;
        font-size: 11px;
        selection-background-color: {c['primary']};
    }}

    /* ── Status bar ─────────────────────── */
    QStatusBar {{
        background-color: {c['status_bg']};
        color: {c['text_primary']};
        font-size: 12px;
        border-top: 1px solid {c['border']};
        padding: 4px 12px;
    }}

    /* ── Labels ─────────────────────────── */
    QLabel#sectionHeader {{
        font-size: 16px;
        font-weight: bold;
        color: {c['text_primary']};
    }}
    QLabel#rowLabel {{
        font-size: 12px;
        color: {c['text_secondary']};
        padding-left: 24px;
    }}
    QLabel#rowLabelBold {{
        font-size: 12px;
        font-weight: bold;
        color: {c['text_primary']};
        padding-left: 8px;
    }}
    QLabel#rowValue {{
        font-size: 13px;
        color: {c['text_primary']};
    }}
    QLabel#hint {{
        font-size: 11px;
        color: {c['text_tertiary']};
    }}

    /* ── Scroll area (transparent bg) ──── */
    QScrollArea {{
        background-color: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}

    /* ── Tooltips ───────────────────────── */
    QToolTip {{
        background-color: {c['card_bg']};
        color: {c['text_primary']};
        border: 1px solid {c['border_light']};
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 12px;
    }}

    /* ── Splitter ───────────────────────── */
    QSplitter::handle {{
        background-color: {c['border']};
        width: 1px;
    }}

    /* ── Context menus ──────────────────── */
    QMenu {{
        background-color: {c['card_bg']};
        color: {c['text_primary']};
        border: 1px solid {c['border_light']};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 12px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {c['primary']};
        color: {c['text_white']};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {c['border']};
        margin: 4px 8px;
    }}
    """
