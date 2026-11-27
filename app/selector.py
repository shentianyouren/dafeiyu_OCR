"""全屏框选遮罩。

覆盖整个虚拟桌面，压暗背景，鼠标拖出一个矩形后松手触发识别。
用 CompositionMode_Clear 把选区抠成透明，而不是简单描个框，
这样用户能看清自己到底圈住了哪些像素。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Selector(QWidget):
    selected = Signal(QRect)   # 全局逻辑坐标
    canceled = Signal()

    # 重绘节流：约 60fps。高频鼠标（1000Hz 回报率）如果每次都刷，
    # GUI 线程会被整屏合成占满，拖框就不跟手了。60fps 人眼已经够用。
    REPAINT_MS = 16

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._start: QPoint | None = None
        self._cur: QPoint | None = None
        self._dirty = QRect()
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(self.REPAINT_MS)
        self._repaint_timer.timeout.connect(self._flush)

    # ---------- 重绘调度 ----------

    def _box_rect(self) -> QRect:
        """选区连同边框、角标、右下角尺寸标签的包围盒。"""
        if self._start is None or self._cur is None:
            return QRect()
        r = QRect(self._start, self._cur).normalized()
        # 右侧多留 104px 给尺寸标签，下方 44px 同理
        return r.adjusted(-8, -8, 104, 44)

    def _schedule(self, rect: QRect) -> None:
        if rect.isNull():
            return
        self._dirty = rect if self._dirty.isNull() else self._dirty.united(rect)
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _flush(self) -> None:
        r = self._dirty
        self._dirty = QRect()
        if not r.isNull():
            self.update(r)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        if geo.isEmpty():
            geo = QGuiApplication.primaryScreen().geometry()
        self._start = None
        self._cur = None
        self._dirty = QRect()
        self._repaint_timer.stop()
        self.setGeometry(geo)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.repaint()

    def _finish(self, rect: QRect | None) -> None:
        # 关键：遮罩必须先从屏幕上真正消失，再让主程序去抓图。
        # 否则抓到的这一帧里还压着半透明黑幕和蓝色选框。
        # 桌面合成器需要一两帧才会把它撤掉，所以这里留 90ms 缓冲。
        self.setWindowOpacity(0.0)
        self.hide()
        self._repaint_timer.stop()
        self._dirty = QRect()
        self._start = None
        self._cur = None
        QTimer.singleShot(90, lambda: self._emit_finished(rect))

    def _emit_finished(self, rect: QRect | None) -> None:
        self.setWindowOpacity(1.0)
        if rect is None:
            self.canceled.emit()
        else:
            self.selected.emit(rect)

    # ---------- 事件 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._finish(None)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._start = e.position().toPoint()
            self._cur = self._start
            # 按下这一帧要擦掉顶部的操作提示，只此一次整屏刷，代价可以忽略
            self._dirty = QRect()
            self._repaint_timer.stop()
            self.update()

    def mouseMoveEvent(self, e):
        if self._start is None:
            return
        old = self._box_rect()
        self._cur = e.position().toPoint()
        new = self._box_rect()
        # 只重绘旧框和新框的并集。整屏全刷会把单核吃满，
        # 尤其 4K 屏面积更大，拖框直接变卡。
        self._schedule(old.united(new))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        rect = QRect(self._start, e.position().toPoint()).normalized()
        if rect.width() < 6 or rect.height() < 6:
            # 手抖点了一下，当作取消，避免识别一个像素
            self._finish(None)
            return
        self._finish(rect.translated(self.geometry().topLeft()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._finish(None)

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        p = QPainter(self)
        # 画的全是直角矩形和 1~3px 直线，抗锯齿没有收益，反而让每帧
        # 多一层边缘混合。尺寸标签的圆角请看得过去就行，优先保证拖框跟手。
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self._start and self._cur:
            r = QRect(self._start, self._cur).normalized()

            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            pen = QPen(QColor(55, 138, 221), 2)
            p.setPen(pen)
            p.drawRect(r.adjusted(0, 0, -1, -1))

            # 四个角加粗，方便看清边界
            p.setPen(QPen(QColor(55, 138, 221), 3))
            arm = min(14, r.width() // 3, r.height() // 3)
            for cx, cy, dx, dy in (
                (r.left(), r.top(), 1, 1),
                (r.right(), r.top(), -1, 1),
                (r.left(), r.bottom(), 1, -1),
                (r.right(), r.bottom(), -1, -1),
            ):
                p.drawLine(cx, cy, cx + dx * arm, cy)
                p.drawLine(cx, cy, cx, cy + dy * arm)

            self._draw_size_tag(p, r)
        else:
            self._draw_hint(p)

    def _draw_hint(self, p: QPainter) -> None:
        text = "拖动鼠标框选要识别的区域    Esc 取消"
        font = QFont()
        font.setPixelSize(15)
        p.setFont(font)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 32
        h = 40
        x = (self.width() - w) // 2
        y = max(40, self.height() // 12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 24, 205))
        p.drawRoundedRect(QRect(x, y, w, h), 10, 10)
        p.setPen(QColor(235, 235, 240))
        p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_size_tag(self, p: QPainter, r: QRect) -> None:
        font = QFont()
        font.setPixelSize(12)
        p.setFont(font)
        text = f"{r.width()} x {r.height()}"
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 16
        h = 24

        # 优先放选区右下外侧，贴边时翻到内侧
        tx = r.right() + 6
        ty = r.bottom() + 6
        if tx + w > self.width():
            tx = max(0, r.right() - w)
        if ty + h > self.height():
            ty = max(0, r.top() - h - 6)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 24, 215))
        p.drawRoundedRect(QRect(tx, ty, w, h), 6, 6)
        p.setPen(QColor(235, 235, 240))
        p.drawText(QRect(tx, ty, w, h), Qt.AlignmentFlag.AlignCenter, text)
