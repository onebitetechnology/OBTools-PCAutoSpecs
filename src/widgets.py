"""
PC AutoSpec — Reusable Qt widgets.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QPushButton,
)
from theme import COLORS


class RoundedCard(QFrame):
    """A QFrame that gets rounded-corner styling via QSS #card selector."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self._layout.setSpacing(0)

    @property
    def inner_layout(self):
        return self._layout


class InfoRow(QWidget):
    """Single label:value row with alternating background.

    If ``on_click`` is set, the row becomes clickable (pointer cursor,
    chevron indicator, hover highlight).
    """

    def __init__(self, label_text, value_text="Detecting...", bold=False,
                 row_index=0, color=None, parent=None):
        super().__init__(parent)
        self._bg = COLORS['card_bg'] if row_index % 2 == 0 else COLORS['row_alt']
        self._on_click = None
        self.setStyleSheet(f"background-color: {self._bg}; border-radius: 0px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        self.label = QLabel(f"{label_text}:")
        self.label.setObjectName("rowLabelBold" if bold else "rowLabel")
        self.label.setMinimumWidth(160)
        self.label.setMaximumWidth(180)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.label)

        self.value = QLabel(value_text)
        self.value.setObjectName("rowValue")
        self.value.setWordWrap(True)
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        if color:
            self.value.setStyleSheet(f"color: {color};")
        layout.addWidget(self.value, 1)

        # Chevron indicator (hidden until clickable)
        self._chevron = QLabel("\u203a")
        self._chevron.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 16px; "
            f"padding: 0 4px;")
        self._chevron.hide()
        layout.addWidget(self._chevron)

    def set_value(self, text, color=None):
        self.value.setText(text)
        if color:
            self.value.setStyleSheet(f"color: {color};")
        else:
            self.value.setStyleSheet("")

    def set_click_handler(self, callback):
        """Make this row clickable. ``callback`` is called on click."""
        self._on_click = callback
        self.setCursor(Qt.PointingHandCursor)
        self._chevron.show()

    def clear_click_handler(self):
        """Remove clickable behavior."""
        self._on_click = None
        self.unsetCursor()
        self._chevron.hide()

    def mousePressEvent(self, event):
        if self._on_click and event.button() == Qt.LeftButton:
            self._on_click()
        else:
            super().mousePressEvent(event)

    def enterEvent(self, event):
        if self._on_click:
            self.setStyleSheet(
                f"background-color: {COLORS['border']}; border-radius: 0px;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._on_click:
            self.setStyleSheet(
                f"background-color: {self._bg}; border-radius: 0px;")
        super().leaveEvent(event)


class DetailRow(QWidget):
    """Indented sub-detail row (e.g., CPU generation, RAM slot info)."""

    def __init__(self, text, color=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {COLORS['card_bg']}; border-radius: 0px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 3, 8, 3)
        layout.setSpacing(0)

        self.label = QLabel(text)
        self.label.setObjectName("rowLabel")
        self.label.setWordWrap(True)
        if color:
            self.label.setStyleSheet(f"color: {color}; padding-left: 0px;")
        else:
            self.label.setStyleSheet("padding-left: 0px;")
        layout.addWidget(self.label, 1)

    def set_text(self, text, color=None):
        self.label.setText(text)
        if color:
            self.label.setStyleSheet(f"color: {color}; padding-left: 0px;")


class Separator(QFrame):
    """Thin horizontal line separator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background-color: {COLORS['border']}; border: none;")


class InfoSection(QWidget):
    """A section with a header label, separator, and dynamic content area."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._title = title
        self._row_count = 0
        self._static_rows = {}   # name → InfoRow (pre-created, always present)
        self._dynamic_widgets = []  # widgets added after spec collection

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 20)
        layout.setSpacing(0)

        # Section header
        header = QLabel(title)
        header.setObjectName("sectionHeader")
        header.setContentsMargins(4, 0, 0, 6)
        layout.addWidget(header)

        # Separator under header
        layout.addWidget(Separator())

        # Content area for rows
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        layout.addWidget(self._content)

    def add_static_row(self, name, label_text, bold=True, color=None):
        """Add a pre-created row that persists across updates."""
        row = InfoRow(label_text, "Detecting...", bold=bold,
                      row_index=self._row_count, color=color)
        self._row_count += 1
        self._content_layout.addWidget(row)
        self._static_rows[name] = row
        return row

    def set_row_value(self, name, text, color=None):
        """Update a static row's value."""
        row = self._static_rows.get(name)
        if row:
            row.set_value(text, color)

    def add_separator(self):
        """Add a visual separator."""
        sep = Separator()
        self._content_layout.addWidget(sep)
        self._dynamic_widgets.append(sep)

    def add_group_gap(self):
        """Add a visible gap between groups (e.g., between drives, adapters)."""
        spacer = QFrame()
        spacer.setFixedHeight(8)
        spacer.setStyleSheet("background: transparent; border: none;")
        self._content_layout.addWidget(spacer)
        self._dynamic_widgets.append(spacer)

    def add_detail_row(self, text, color=None):
        """Add a dynamic detail row."""
        row = DetailRow(text, color)
        self._content_layout.addWidget(row)
        self._dynamic_widgets.append(row)
        return row

    def add_info_row(self, label_text, value_text, bold=False, color=None):
        """Add a dynamic info row (label:value pair)."""
        row = InfoRow(label_text, value_text, bold=bold,
                      row_index=self._row_count, color=color)
        self._row_count += 1
        self._content_layout.addWidget(row)
        self._dynamic_widgets.append(row)
        return row

    def add_heading(self, text):
        """Add a sub-heading row (e.g., drive name, adapter name).

        Participates in the alternating row system for visual consistency.
        """
        bg = COLORS['card_bg'] if self._row_count % 2 == 0 else COLORS['row_alt']
        self._row_count += 1
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {COLORS['text_secondary']}; "
            f"background-color: {bg}; padding: 6px 8px; border-radius: 0px;"
        )
        self._content_layout.addWidget(lbl)
        self._dynamic_widgets.append(lbl)

    def clear_dynamic(self):
        """Remove all dynamically-added widgets, keep static rows."""
        for w in self._dynamic_widgets:
            self._content_layout.removeWidget(w)
            w.deleteLater()
        self._dynamic_widgets.clear()
        # Reset row counter to static count for alternating colors
        self._row_count = len(self._static_rows)


class SpinnerDot(QWidget):
    """Tiny widget that draws a spinning arc."""

    def __init__(self, size=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0

    def set_angle(self, angle):
        self._angle = angle
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(COLORS['primary']), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        r = self.rect().adjusted(3, 3, -3, -3)
        p.drawArc(r, self._angle * 16, 270 * 16)
        p.end()


class SpinnerWidget(QWidget):
    """Animated spinner with phase text label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._dot = SpinnerDot(20, self)
        layout.addWidget(self._dot)

        self._label = QLabel("Collecting system specifications...")
        self._label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        layout.addWidget(self._label)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

    def start(self):
        self.show()
        self._timer.start(40)

    def stop(self):
        self._timer.stop()
        self.hide()

    def set_phase(self, text):
        self._label.setText(text)

    def _rotate(self):
        self._angle = (self._angle + 18) % 360
        self._dot.set_angle(self._angle)


class NotificationBar(QFrame):
    """Dismissible notification banner."""
    dismissed = Signal()

    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.setObjectName("notificationBar")
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)

        icon = QLabel("\u2699")
        icon.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        layout.addWidget(icon)

        msg = QLabel(message)
        msg.setStyleSheet("color: white; font-size: 12px; font-weight: bold;")
        layout.addWidget(msg)
        layout.addStretch()

        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; "
            "background: transparent; border: none; border-radius: 4px;"
        )
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._dismiss)
        layout.addWidget(close_btn)

        QTimer.singleShot(12000, self._dismiss)

    def _dismiss(self):
        self.hide()
        self.dismissed.emit()
        self.deleteLater()
