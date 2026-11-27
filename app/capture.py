"""屏幕抓取。

核心难点：浏览器、播放器、游戏是 GPU 直接渲染的，不走 GDI。
用 ImageGrab / mss 这类 BitBlt 方案去抓，会得到一张纯黑图。
所以主路径必须是 DXGI Desktop Duplication（直接从帧缓冲取图）。

抓取失败时按 dxgi -> gdi 降级，并在返回值里标明用的哪条路径，
方便用户在预览窗里看到"这次可能抓到黑图"的提示。
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """整个虚拟桌面（含所有显示器）的物理像素范围。"""
    u = ctypes.windll.user32
    x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, x + w, y + h


def primary_screen_rect() -> tuple[int, int, int, int]:
    u = ctypes.windll.user32
    x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = u.GetSystemMetrics(SM_CXSCREEN)
    h = u.GetSystemMetrics(SM_CYSCREEN)
    return x, y, x + w, y + h


@dataclass
class GrabResult:
    image: np.ndarray          # HxWx3, RGB
    backend: str               # dxgi / gdi
    region: tuple[int, int, int, int]
    warning: Optional[str] = None


class ScreenGrabber:
    """主屏走 DXGI，其余情况降级到 GDI。"""

    def __init__(self, logger=None):
        self._log = logger or (lambda m: None)
        self._camera = None
        self._dxgi_ok = False
        self._origin = (0, 0)
        self._init_dxgi()

    def _init_dxgi(self) -> None:
        try:
            import dxcam  # noqa: F401
        except Exception as e:
            self._log(f"DXcam 不可用，将退化为 GDI 抓屏：{e}")
            return
        try:
            # 主显示器在虚拟桌面里的左上角，用来把全局坐标换算成 DXGI 的本地坐标
            self._origin = (ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
                            ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
            # processor_backend 必须指定 numpy：dxcam 默认走 cv2 做颜色转换，
            # 精简版为了省 60MB 把 opencv 排除了，走默认就会每次抓屏都抛
            # ModuleNotFoundError 然后悄悄退化成 GDI —— 抓视频又变黑屏。
            # numpy 后端用的是 dxcam 自带的 Cython 内核，不需要 opencv。
            try:
                self._camera = dxcam.create(output_idx=0, output_color="RGB",
                                            processor_backend="numpy")
            except TypeError:
                # 旧版 dxcam 没有这个参数
                self._camera = dxcam.create(output_idx=0, output_color="RGB")
            if self._camera is None:
                raise RuntimeError("dxcam.create 返回 None")
            self._dxgi_ok = True
            self._log("抓屏后端：DXGI 桌面复制（可抓硬件加速窗口）")
        except Exception as e:
            self._log(f"DXGI 初始化失败，退化为 GDI：{e}")
            self._camera = None
            self._dxgi_ok = False

    @property
    def backend(self) -> str:
        return "dxgi" if self._dxgi_ok else "gdi"

    # ---------- 抓取 ----------

    def grab(self, rect: tuple[int, int, int, int]) -> GrabResult:
        l, t, r, b = [int(round(v)) for v in rect]
        if r <= l or b <= t:
            raise ValueError(f"选区无效：{rect}")

        if self._dxgi_ok:
            img = self._grab_dxgi((l, t, r, b))
            if img is not None:
                return GrabResult(img, "dxgi", (l, t, r, b))
            self._log("DXGI 未取到帧，本次改用 GDI")

        img = self._grab_gdi((l, t, r, b))
        warn = None
        if _looks_blank(img):
            warn = ("抓到的画面疑似全黑。若正在播放受版权保护的内容"
                    "（如 Netflix），系统会阻止任何形式的截屏。")
        return GrabResult(img, "gdi", (l, t, r, b), warn)

    def _grab_dxgi(self, rect: tuple[int, int, int, int]):
        if not self._camera:
            return None
        ox, oy = self._origin
        # DXGI 的 region 是相对主显示器左上角的本地坐标
        local = (rect[0] - ox, rect[1] - oy, rect[2] - ox, rect[3] - oy)

        px, py, pw, ph = primary_screen_rect()
        if not (local[0] >= 0 and local[1] >= 0 and local[2] <= pw and local[3] <= ph):
            # 选区跑到副屏去了，DXGI 主相机够不着
            return None
        if local[2] <= local[0] or local[3] <= local[1]:
            return None

        for attempt in range(6):
            try:
                try:
                    frame = self._camera.grab(region=local, new_frame_only=False)
                except TypeError:
                    frame = self._camera.grab(region=local)
            except Exception as e:
                self._log(f"DXGI 抓取异常：{e}")
                return None
            if frame is not None and getattr(frame, "size", 0):
                return np.ascontiguousarray(frame)
            time.sleep(0.012)
        return None

    def _grab_gdi(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=rect, all_screens=True)
            return np.array(img.convert("RGB"))
        except Exception as e:
            self._log(f"GDI 抓屏失败：{e}")
            l, t, r, b = rect
            return np.zeros((max(1, b - t), max(1, r - l), 3), dtype=np.uint8)

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None
            self._dxgi_ok = False


def _looks_blank(img: np.ndarray) -> bool:
    if img is None or img.size == 0:
        return True
    sample = img[:: max(1, img.shape[0] // 32), :: max(1, img.shape[1] // 32)]
    return float(sample.std()) < 1.0 and float(sample.mean()) < 6.0
