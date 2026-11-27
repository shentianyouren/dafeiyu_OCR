"""轻量提示条。不抢焦点，几秒后自己消失。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget


class Toast(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, anchor: QPoint | None = None, ms: int = 1600) -> None:
        self._text = text
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        self.setFont(font)
        fm = self.fontMetrics()
        w = min(640, fm.horizontalAdvance(text) + 36)
        h = 38
        self.resize(w, h)

        if anchor is None:
            anchor = QGuiApplication.primaryScreen().availableGeometry().center()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = anchor.x() + 14
        y = anchor.y() + 20
        if x + w > geo.right():
            x = max(geo.left() + 8, geo.right() - w - 8)
        if y + h > geo.bottom():
            y = max(geo.top() + 8, anchor.y() - h - 20)
        self.move(x, y)

        self.show()
        self.raise_()
        self.update()
        self._timer.start(ms)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QColor(60, 60, 66, 220))
        p.setBrush(QColor(24, 24, 28, 218))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor(240, 240, 244))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
