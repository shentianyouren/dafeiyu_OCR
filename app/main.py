"""程序入口：把悬浮球、框选、抓屏、识别、预览串成一条链路。"""

from __future__ import annotations

import ctypes
import sys
import time
import traceback
from pathlib import Path

# 保证 paths 最先导入，把模型缓存目录钉死在项目内
from . import paths  # noqa: F401
from . import config

from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .capture import ScreenGrabber
from .floating_ball import FloatingBall
from .hotkey import HotkeyManager
from .ocr_engine import LocalEngine, OcrResult, suspect_formula
from .texo_engine import TexoEngine
from .preview import PreviewWindow
from .selector import Selector
from .settings import SettingsWindow
from .toast import Toast

MUTEX_NAME = "Global\\SanshiOcr_SingleInstance_9F2A"


def make_logger():
    logfile = paths.LOGS_DIR / "app.log"

    def log(msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        try:
            print(line)
        except Exception:
            pass
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    return log


class OcrWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, engine: LocalEngine, image, mode: str, log,
                 engine_override: str | None = None):
        super().__init__()
        self._engine = engine
        self._image = image
        self._mode = mode
        self._log = log
        self._override = engine_override

    def run(self):
        try:
            if self._mode == "formula":
                res = self._engine.recognize_formula(self._image)
            else:
                res = self._engine.recognize(self._image, engine_override=self._override)
            self.done.emit(res)
        except Exception as e:
            self._log(f"识别线程异常：{e}\n{traceback.format_exc()}")
            self.failed.emit(str(e))


class WarmupWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, engine: LocalEngine):
        super().__init__()
        self._engine = engine

    def run(self):
        ok = self._engine.warmup()
        # 公式模型也顺手热一下：模型只有 0.2 秒，但第一次点会等它加载
        if self._engine.formula is not None:
            self._engine.formula.warmup()
        self.done.emit(ok, self._engine.init_error or "")


class SanshiApp:
    def __init__(self):
        self.log = make_logger()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("识屏识字")
        self.app.setWindowIcon(_make_icon())

        self.cfg = config.load()
        self._worker: OcrWorker | None = None
        self._warmup: WarmupWorker | None = None
        self._last_image = None
        self._pending_anchor = QPoint(0, 0)
        self._ball_was_visible = True
        self._capture_pending = False

        self.log("=== 启动 ===")
        self._check_dpi_consistency()

        self.grabber = ScreenGrabber(self.log)
        self.formula = TexoEngine(logger=self.log)
        self.engine = LocalEngine(
            text_engine=self.cfg.get("text_engine", "fast"),
            logger=self.log,
            formula=self.formula if self.formula.available else None,
        )

        self.ball = FloatingBall(
            size=int(self.cfg.get("ball_size", 52)),
            opacity=float(self.cfg.get("ball_opacity", 0.92)),
            logger=self.log,
        )
        self.selector = Selector()
        self.preview = PreviewWindow()
        self.toast = Toast()
        self.settings_win = SettingsWindow(self.cfg)
        self.hotkey = HotkeyManager(self.app, self.trigger_capture, self.log)

        self._wire()
        self._setup_tray()

    # ---------- 初始化 ----------

    def _wire(self) -> None:
        self.ball.clicked.connect(self.trigger_capture)
        self.ball.request_settings.connect(self.open_settings)
        self.ball.request_quit.connect(self.quit)
        self.ball.moved.connect(self._on_ball_moved)

        self.selector.selected.connect(self._on_selected)
        self.selector.canceled.connect(self._on_canceled)

        self.preview.retry_requested.connect(self._retry_better)

        self.settings_win.saved.connect(self._on_settings_saved)

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_icon(), self.app)
        menu = QMenu()
        menu.addAction("框选识别", self.trigger_capture)
        menu.addAction("显示悬浮球", self._show_ball)
        menu.addAction("设置", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("识屏识字")
        self.tray.activated.connect(
            lambda reason: self.trigger_capture()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.tray.show()

    def _show_ball(self) -> None:
        self.ball.show()
        self.ball.raise_()

    def _on_ball_moved(self, x: int, y: int) -> None:
        self.cfg = config.update(ball_position={"x": x, "y": y})

    def _check_dpi_consistency(self) -> None:
        ratios = {round(s.devicePixelRatio(), 2) for s in QGuiApplication.screens()}
        if len(ratios) > 1:
            self.log(f"注意：多显示器缩放比例不一致 {ratios}，副屏坐标换算可能有偏差")

    # ---------- 主链路 ----------

    def trigger_capture(self) -> None:
        if self.selector.isVisible() or self._capture_pending:
            return
        self.preview.hide()
        # 先让气泡变成「识图中...」。中间这 0.65 秒是留给用户看反馈的：
        # 点了有反应，比"点完立刻黑屏"更像有人应答。
        self.ball.set_state("working")
        self._ball_was_visible = self.ball.isVisible()
        self._capture_pending = True
        QTimer.singleShot(650, self._begin_selection)

    def _begin_selection(self) -> None:
        self._capture_pending = False
        if self.selector.isVisible():
            return
        # 悬浮球是置顶的，如果它正好落在框选范围内会被一起截进图里，
        # 识别结果就多出一个人和气泡。所以框选期间必须收起来。
        self.ball.hide()
        self.selector.start()

    def _restore_ball(self) -> None:
        if self._ball_was_visible and not self.ball.isVisible():
            self.ball.show()

    def _on_canceled(self) -> None:
        self.log("已取消框选")
        self._restore_ball()
        self.ball.set_state("idle")

    def _on_selected(self, rect: QRect) -> None:
        try:
            self._handle_selection(rect)
        finally:
            self._restore_ball()

    def _handle_selection(self, rect: QRect) -> None:
        anchor = QCursor.pos()
        self._pending_anchor = anchor

        if self._worker is not None and self._worker.isRunning():
            self.toast.show_message("上一次识别还没结束，请稍等", anchor, 1800)
            return

        try:
            phys = self._to_physical(rect)
        except Exception as e:
            self.toast.show_message(f"坐标换算失败：{e}", anchor, 2600)
            self.log(f"坐标换算失败：{e}")
            return

        self.log(f"选区 逻辑={rect.getRect()} 物理={phys}")

        try:
            grab = self.grabber.grab(phys)
        except Exception as e:
            self.toast.show_message(f"抓屏失败：{e}", anchor, 2600)
            self.log(f"抓屏失败：{e}")
            return

        img = grab.image
        if img is None or img.size == 0:
            self.toast.show_message("抓屏结果为空，请重试", anchor, 2600)
            return

        self._last_image = img
        self.log(f"抓屏完成 backend={grab.backend} shape={img.shape}")

        mode = self.cfg.get("engine", "local")
        if mode == "auto":
            mode = "local"

        hint = "识别中…"
        if not self.engine.ready:
            hint = "首次识别需加载模型，约 30 秒…"
        self.toast.show_message(hint, anchor, 120000)
        self._start_worker(img, mode, anchor, grab.warning)

    def _start_worker(self, image, mode: str, anchor: QPoint, grab_warning,
                      engine_override: str | None = None) -> None:
        self.preview.reset_title_style()
        worker = OcrWorker(self.engine, image, mode, self.log, engine_override)
        worker.done.connect(lambda res: self._on_result(res, anchor, grab_warning))
        worker.failed.connect(lambda msg: self._on_failed(msg, anchor))
        self._worker = worker
        worker.start()

    def _on_result(self, res: OcrResult, anchor: QPoint, grab_warning) -> None:
        self.toast.hide()
        self.log(f"识别完成 engine={res.engine} 耗时={res.elapsed:.2f}s "
                 f"公式={res.has_formula} 长度={len(res.plain)}")

        if grab_warning and not res.warning:
            res.warning = grab_warning
        if not (res.plain or res.markdown) and not res.warning:
            res.warning = "没识别到文字。试试框得更贴合一些，或框小一点。"
        elif res.engine == "local" and not res.warning and suspect_formula(res.plain):
            # 走到这里说明公式通道没接住（模型缺失，或者输出没通过体检）
            res.warning = "这段里像是有公式，但没转成 LaTeX。可以点「重识」让公式模型再试一次。"

        can_retry = self.engine.formula is not None
        show_panel = bool(self.cfg.get("show_preview", True))
        text = res.markdown or res.plain

        # 不管弹不弹窗，先塞进剪贴板。这样用户就算不看预览窗，
        # 直接 Ctrl+V 也有内容；不满意再回窗里改。
        if text:
            QApplication.clipboard().setText(text)

        if show_panel or res.warning or not res.plain:
            self.preview.show_result(res, anchor, can_retry)
        elif text:
            self.toast.show_message("已复制到剪贴板", anchor, 1500)

        self.ball.set_state("done" if text else "idle")
        QTimer.singleShot(1600, lambda: self.ball.set_state("idle"))

    def _on_failed(self, msg: str, anchor: QPoint) -> None:
        self.toast.hide()
        self.preview.reset_title_style()      # 按钮别一直卡在「识别中…」
        self.toast.show_message(f"识别出错：{msg}", anchor, 4000)
        self.ball.set_state("idle")

    def _retry_better(self) -> None:
        """重识：框的确实是公式就直接走公式模型，否则换增强文字引擎。"""
        if self._last_image is None:
            self.toast.show_message("没有可重识的图片", QCursor.pos(), 1800)
            return
        anchor = QCursor.pos()
        self.ball.set_state("working")
        if self.engine.formula is not None:
            hint = "公式识别中…"
            self._start_worker(self._last_image, "formula", anchor, None)
        else:
            hint = "增强引擎识别中，约 2 秒…"
            self._start_worker(self._last_image, "local", anchor, None, "accurate")
        self.toast.show_message(hint, anchor, 60000)

    def _to_physical(self, rect: QRect) -> tuple[int, int, int, int]:
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio() or 1.0)
        return (
            int(round(rect.x() * dpr)),
            int(round(rect.y() * dpr)),
            int(round((rect.x() + rect.width()) * dpr)),
            int(round((rect.y() + rect.height()) * dpr)),
        )

    # ---------- 设置 ----------

    def open_settings(self) -> None:
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()

    def _on_settings_saved(self, patch: dict) -> None:
        self.cfg = config.update(**patch)
        ok, msg = self.hotkey.register(self.cfg.get("hotkey", "ctrl+shift+o"))
        if not ok:
            self.toast.show_message(msg, QCursor.pos(), 4200)
            self.log(msg)
        self.engine.text_engine = self.cfg.get("text_engine", "fast")
        self.ball.set_opacity_level(float(self.cfg.get("ball_opacity", 0.92)))
        self.log("设置已保存")

    # ---------- 启动 / 退出 ----------

    def run(self) -> int:
        self.ball.restore_position(self.cfg.get("ball_position"))
        self.ball.show()
        self.ball.raise_()

        ok, msg = self.hotkey.register(self.cfg.get("hotkey", "ctrl+shift+o"))
        if not ok:
            self.log(msg)
            QTimer.singleShot(900, lambda: self.toast.show_message(
                msg + "，可到设置里换一个", QCursor.pos(), 4200))
        else:
            self.toast.show_message(
                f"识屏识字已启动，按 {self.cfg.get('hotkey')} 或点悬浮球开始",
                QCursor.pos(), 2600)

        # 延后预热，不拖慢启动；模型从项目 models 目录加载
        QTimer.singleShot(1500, self._start_warmup)
        return self.app.exec()

    def _start_warmup(self) -> None:
        self._warmup = WarmupWorker(self.engine)
        self._warmup.done.connect(self._on_warmup_done)
        self._warmup.start()

    def _on_warmup_done(self, ok: bool, err: str) -> None:
        if ok:
            self.log("模型预热完成，可以开始识别")
        else:
            self.log(f"模型预热失败：{err}")
            self.toast.show_message(f"本地模型加载失败：{err[:60]}", QCursor.pos(), 5000)

    def quit(self) -> None:
        self.log("=== 退出 ===")
        try:
            self.hotkey.unregister()
        except Exception:
            pass
        try:
            self.grabber.close()
        except Exception:
            pass
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        if self._warmup is not None and self._warmup.isRunning():
            self._warmup.wait(2000)
        try:
            self.tray.hide()
        except Exception:
            pass
        self.app.quit()


def _make_icon() -> QIcon:
    """托盘和任务栏图标。

    优先用 assets 里的图标（和悬浮球同一张头像），读不到才退回画的蓝球，
    免得哪天资源没打进去整出个空图标。
    """
    for name in ("icon.ico", "person.png"):
        p = paths.ASSETS_DIR / name
        try:
            if p.exists():
                icon = QIcon(str(p))
                if not icon.isNull():
                    return icon
        except Exception:
            continue
    return _fallback_icon()


def _fallback_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(24, 95, 165))
    p.drawEllipse(2, 2, 60, 60)
    f = QFont("Microsoft YaHei")
    f.setPixelSize(34)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "识")
    p.end()
    return QIcon(pm)


def _already_running() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if _already_running():
        app = QApplication(sys.argv)
        box = Toast()
        box.show_message("识屏识字已经在运行了（看右下角托盘）", QCursor.pos(), 3200)
        QTimer.singleShot(3400, app.quit)
        return app.exec()

    app = SanshiApp()
    return app.run()
