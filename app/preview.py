"""识别结果预览窗。

有意做成「显示时不抢焦点」：你在全屏看视频，识别完弹出一个卡片，
视频不会因此退出全屏，也不会暂停。想改错字再点一下就行。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .ocr_engine import OcrResult

STYLE = """
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #D3D1C7;
    border-radius: 12px;
}
QLabel#title {
    color: #2C2C2A;
    font-size: 13px;
    font-weight: 500;
}
QLabel#meta {
    color: #888780;
    font-size: 11px;
}
QLabel#warn {
    color: #A32D2D;
    font-size: 11px;
}
QTextEdit {
    background: #FAFAF8;
    border: 1px solid #E6E4DC;
    border-radius: 8px;
    padding: 6px;
    color: #1F1F1D;
    font-size: 13px;
    selection-background-color: #B5D4F4;
}
QPushButton {
    background: #F1EFE8;
    border: 1px solid #D3D1C7;
    border-radius: 7px;
    padding: 5px 12px;
    color: #2C2C2A;
    font-size: 12px;
}
QPushButton:hover { background: #E6E4DC; }
QPushButton#primary {
    background: #185FA5;
    border: 1px solid #185FA5;
    color: #FFFFFF;
}
QPushButton#primary:hover { background: #0C447C; }
QPushButton#close {
    background: transparent;
    border: none;
    color: #888780;
    font-size: 15px;
    padding: 0 4px;
}
QPushButton#close:hover { color: #A32D2D; }
"""


class _DragFrame(QFrame):
    """卡片容器。

    卡片上除了编辑器和按钮，还有标题栏、各处留白。这些地方的鼠标事件
    在 QLabel 忽略之后会落到这里，转给顶层窗口去做拖动。
    不能直接给 card 设 WA_TransparentForMouseEvents —— 那个属性连子控件
    一起屏蔽，编辑器和按钮就全点不动了。
    """

    def mousePressEvent(self, e):
        win = self.window()
        if win is not self:
            win.mousePressEvent(e)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.window().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.window().mouseReleaseEvent(e)


class PreviewWindow(QWidget):
    retry_requested = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)

        self._result: OcrResult | None = None
        self._editable_text = ""
        # 拖动支持：记录按下时的光标偏移，以及用户拖出来的相对位置
        self._drag_offset: QPoint | None = None
        self._anchor: QPoint | None = None
        self._user_offset = (14, 14)

        card = _DragFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("识别结果")
        self.title.setObjectName("title")
        self.title.setCursor(Qt.CursorShape.OpenHandCursor)
        self.meta = QLabel("")
        self.meta.setObjectName("meta")
        self.meta.setCursor(Qt.CursorShape.OpenHandCursor)
        btn_close = QPushButton("×")
        btn_close.setObjectName("close")
        btn_close.setFixedSize(20, 20)
        btn_close.clicked.connect(self.hide_panel)
        head.addWidget(self.title)
        head.addWidget(self.meta)
        head.addStretch(1)
        head.addWidget(btn_close)
        root.addLayout(head)

        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        root.addWidget(self.warn)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(mono)
        self.editor.setMinimumHeight(120)
        root.addWidget(self.editor, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_copy = QPushButton("复制")
        self.btn_copy.setObjectName("primary")
        self.btn_copy.clicked.connect(self._copy_current)
        self.btn_plain = QPushButton("复制并关闭")
        self.btn_plain.clicked.connect(self._copy_and_close)
        self.btn_retry = QPushButton("重识")
        self.btn_retry.setToolTip("用公式模型重新识别这张图")
        self.btn_retry.clicked.connect(self._retry)
        self.btn_retry.hide()
        foot.addWidget(self.btn_copy)
        foot.addWidget(self.btn_plain)
        foot.addWidget(self.btn_retry)
        foot.addStretch(1)
        root.addLayout(foot)

        self.resize(440, 280)

    # ---------- 展示 ----------

    def show_result(self, result: OcrResult, anchor: QPoint, can_retry: bool) -> None:
        self._result = result
        self._anchor = QPoint(anchor)
        body = result.markdown or result.plain
        self._editable_text = body
        self.editor.setPlainText(body)

        engine_name = {"local": "本地文字", "formula": "公式模型"}.get(result.engine, result.engine)
        pieces = [result.detail or engine_name, f"{result.elapsed * 1000:.0f} ms"]
        if result.has_formula:
            pieces.append("含公式")
        self.meta.setText("  ·  ".join(pieces))
        self.title.setText("识别结果" if body else "没识别到文字")

        if result.warning:
            self.warn.setText(result.warning)
            self.warn.show()
        else:
            self.warn.hide()

        # 本地引擎识别后总留一条"换引擎再来一次"的退路
        self.btn_retry.setVisible(result.engine in ("local", "formula"))

        # 高度随内容走，长文本给到上限
        lines = max(1, body.count("\n") + 1)
        height = min(380, max(200, 132 + lines * 21))
        self.resize(440, height)

        self._place_near(anchor)
        self.show()
        self.raise_()
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def _place_near(self, anchor: QPoint) -> None:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        # 用用户拖出来的相对位置，没拖过就是默认的右下偏移。
        # 这样拖一次之后，以后每次识别都出现在同一个相对位置。
        dx, dy = self._user_offset
        x = anchor.x() + dx
        y = anchor.y() + dy
        if x + w > geo.right():
            x = max(geo.left() + 8, anchor.x() - w - abs(dx))
        if y + h > geo.bottom():
            y = max(geo.top() + 8, anchor.y() - h - abs(dy))
        x = max(geo.left() + 8, x)
        y = max(geo.top() + 8, y)
        self.move(x, y)

    # ---------- 拖动 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag_offset is not None:
            if self._anchor is not None:
                self._user_offset = (
                    self.x() - self._anchor.x(),
                    self.y() - self._anchor.y(),
                )
            self._drag_offset = None
            e.accept()

    def hide_panel(self) -> None:
        self.hide()
        self.closed.emit()

    # ---------- 动作 ----------

    def _current_text(self) -> str:
        text = self.editor.toPlainText().strip()
        return text or self._editable_text

    def _copy_current(self) -> None:
        text = self._current_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._flash("已复制")

    def _copy_and_close(self) -> None:
        self._copy_current()
        self.hide_panel()

    def _retry(self) -> None:
        self.btn_retry.setEnabled(False)
        self.btn_retry.setText("识别中…")
        self.retry_requested.emit()

    def _flash(self, msg: str) -> None:
        self.title.setText(msg)
        self.title.setStyleSheet("color:#0F6E56;font-size:13px;font-weight:500;")

    def reset_title_style(self) -> None:
        self.title.setStyleSheet("")
        self.btn_retry.setEnabled(True)
        self.btn_retry.setText("重识")
