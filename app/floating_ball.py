"""桌面悬浮球：大肥鱼 + 思考气泡。

三个细节决定它好不好用：

1. 必须 WS_EX_NOACTIVATE —— 点它的时候不能让当前窗口失焦，
   否则你看视频一点悬浮球，全屏就退出来了。
2. 必须区分「点击」和「拖动」，用像素阈值判断，不能靠 Qt 的 clicked 信号。
3. 人物和气泡是两张独立的透明 PNG，叠成一个窗口。气泡里的文字由程序绘制，
   不是图里带的，所以「正在思考 → 识图中...」这种状态切换才能做出来。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import paths

# 布局（逻辑像素）。气泡在左上，人物在右下，两者不重叠。
# 整体比上一版小一圈：气泡 118x102 -> 104x90，人物 131x132 -> 112x113。
# 字号仍是 15px 不缩，气泡缩小后文字在云朵里反而更饱满。
BUBBLE_W, BUBBLE_H = 104, 90
PERSON_W, PERSON_H = 112, 113
PERSON_X, PERSON_Y = 33, 90
WIN_W, WIN_H = 145, 203

# 气泡里写字的位置：按气泡原图中文字中心的相对位置换算（约 53.6% x / 40.3% y）
# 云朵几何中心在 50% 处，文字压在这里才不显偏右
TEXT_CX, TEXT_CY = 52, 36
TEXT_MAX_W = 84

STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_DONE = "done"

STATE_TEXT = {
    STATE_IDLE: "正在思考",
    STATE_WORKING: "识图中...",
    STATE_DONE: "已复制",
}


class FloatingBall(QWidget):
    clicked = Signal()
    request_settings = Signal()
    request_quit = Signal()
    moved = Signal(int, int)

    DRAG_THRESHOLD = 5

    def __init__(self, size: int = 52, opacity: float = 0.92, logger=None):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self._log = logger or (lambda m: None)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.setToolTip("点大肥鱼框选识屏  ·  拖动可移动位置  ·  右键更多")

        self._opacity = max(0.3, min(1.0, opacity))
        self._state = STATE_IDLE
        self._hover = False
        self._pressed = False
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._win_pos: QPoint | None = None
        self._person: QPixmap | None = None
        self._bubble: QPixmap | None = None
        self._assets_loaded = False
        self._assets_dpr = 0.0

        self.resize(WIN_W, WIN_H)
        self._place_default()

    # ---------- 位置 ----------

    def _place_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - 30
        y = geo.top() + geo.height() // 3
        self.move(x, y)

    def restore_position(self, pos: dict | None) -> None:
        if not pos:
            return
        try:
            x, y = int(pos["x"]), int(pos["y"])
        except Exception:
            return
        # 上次拔掉副屏可能导致坐标飞到屏幕外，这里拉回来
        union = QRectF()
        for s in QGuiApplication.screens():
            union = union.united(QRectF(s.geometry()))
        if union.contains(QRectF(x, y, self.width(), self.height())):
            self.move(x, y)
        else:
            self._place_default()

    # ---------- 素材 ----------

    def _load_assets(self) -> None:
        # DPI 变了（换显示器、改缩放）要重新生成，否则会糊
        dpr = self.devicePixelRatioF()
        if self._assets_loaded and abs(dpr - self._assets_dpr) < 0.01:
            return
        self._assets_dpr = dpr
        self._assets_loaded = True
        self._bubble = self._make_pixmap("bubble.png", BUBBLE_W, BUBBLE_H, dpr)
        self._person = self._make_pixmap("person.png", PERSON_W, PERSON_H, dpr)

    def _make_pixmap(self, name: str, w: int, h: int, dpr: float) -> QPixmap | None:
        """先缩到目标物理像素尺寸，绘制时 1:1，不做二次缩放。

        原图一千多像素，直接交给 Qt 的 drawPixmap 缩到一百多逻辑像素，
        走的是双线性插值，头发、蕾丝这类密集细节会碎成一片毛刺。
        这里用 LANCZOS 一次缩到位，边缘干净得多。
        """
        path = paths.ASSETS_DIR / name
        if not path.exists():
            self._log(f"悬浮球素材缺失：{path}")
            return None
        tw, th = max(1, round(w * dpr)), max(1, round(h * dpr))
        try:
            from PIL import Image

            im = Image.open(path).convert("RGBA")
            im = im.resize((tw, th), getattr(Image, "LANCZOS", Image.BICUBIC))
            buf = im.tobytes("raw", "RGBA")
            # QImage 不拷贝 buffer，必须 copy() 之后才能丢开 buf
            qimg = QImage(buf, tw, th, tw * 4, QImage.Format.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            pm.setDevicePixelRatio(dpr)
            return pm
        except Exception as e:
            self._log(f"悬浮球素材缩放失败（{name}）：{e}")
            pm = QPixmap(str(path))
            return None if pm.isNull() else pm

    # ---------- 状态 ----------

    def set_state(self, state: str) -> None:
        if state not in STATE_TEXT or state == self._state:
            return
        self._state = state
        self.update()

    @property
    def state(self) -> str:
        return self._state

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setOpacity(self._opacity)
        self._load_assets()

        if self._bubble is not None:
            p.drawPixmap(QRectF(0, 0, BUBBLE_W, BUBBLE_H), self._bubble,
                         QRectF(self._bubble.rect()))
            self._draw_state_text(p)
        if self._person is not None:
            p.drawPixmap(QRectF(PERSON_X, PERSON_Y, PERSON_W, PERSON_H), self._person,
                         QRectF(self._person.rect()))

        if self._bubble is None and self._person is None:
            # 素材全丢时至少让人看得见一个东西，别整成隐形窗口
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(24, 95, 165, 220))
            p.drawEllipse(QRectF(30, 30, 60, 60))

    def _draw_state_text(self, p: QPainter) -> None:
        text = STATE_TEXT.get(self._state, "")
        if not text:
            return

        font = QFont("Microsoft YaHei")
        font.setPixelSize(15)
        # 长文案自动降一档字号，免得顶出气泡
        if QFontMetrics(font).horizontalAdvance(text) > TEXT_MAX_W:
            font.setPixelSize(13)
        p.setFont(font)

        p.setPen(QColor(45, 45, 48))
        rect = QRectF(TEXT_CX - TEXT_MAX_W / 2, TEXT_CY - 12, TEXT_MAX_W, 24)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    # ---------- 鼠标 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._dragging = False
            self._press_pos = e.globalPosition().toPoint()
            self._win_pos = self.pos()
        elif e.button() == Qt.MouseButton.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if not self._pressed or self._press_pos is None or self._win_pos is None:
            return
        delta = e.globalPosition().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() > self.DRAG_THRESHOLD:
            self._dragging = True
        if self._dragging:
            self.move(self._win_pos + delta)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        was_drag = self._dragging
        self._pressed = False
        self._dragging = False
        self._press_pos = None
        self._win_pos = None

        if was_drag:
            self.moved.emit(self.x(), self.y())
        else:
            self.clicked.emit()

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#FFFFFF;border:1px solid #D3D1C7;border-radius:8px;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:5px;color:#2C2C2A;font-size:12px;}"
            "QMenu::item:selected{background:#E6F1FB;color:#0C447C;}"
        )
        act_shot = menu.addAction("框选识别")
        menu.addSeparator()
        act_settings = menu.addAction("设置")
        act_hide = menu.addAction("隐藏悬浮球")
        menu.addSeparator()
        act_quit = menu.addAction("退出")

        chosen = menu.exec(global_pos)
        if chosen == act_shot:
            self.clicked.emit()
        elif chosen == act_settings:
            self.request_settings.emit()
        elif chosen == act_hide:
            self.hide()
        elif chosen == act_quit:
            self.request_quit.emit()

    def set_opacity_level(self, value: float) -> None:
        self._opacity = max(0.3, min(1.0, value))
        self.update()

    def set_diameter(self, size: int) -> None:
        # 现在尺寸由素材比例决定，这个接口留着只为兼容旧调用
        pass
