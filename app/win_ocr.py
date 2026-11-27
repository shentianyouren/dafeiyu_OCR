"""Windows 系统自带的 OCR 引擎。

这是整个方案里最快的一条路：不下载模型，不占内存，一次识别约 50 毫秒。
代价是它主要面向印刷体，手写和艺术字会明显变弱，所以只让它打头阵，
识别不出来时再交给 RapidOCR 兜底。

另一个坑：它按「词」切分，中文会被拆成单字并在字之间插空格，
所以在取文本时必须把中文字符之间的空格清掉，否则粘贴出来全是散的。
"""

from __future__ import annotations

import asyncio
import io
import re
from typing import Optional

import numpy as np
from PIL import Image

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    WINRT_OK = True
except Exception:  # 没装 winrt 包时优雅降级
    WINRT_OK = False


# 两边都是非 ASCII 字符时，夹在中间的空格是 OCR 硬拆出来的，去掉
_CJK_GAP = re.compile(r"(?<=[^\x00-\x7f])[ \t]+(?=[^\x00-\x7f])")


def tidy_text(text: str) -> str:
    return _CJK_GAP.sub("", text or "").strip()


class WindowsOcr:
    def __init__(self, lang_tag: str = "zh-Hans-CN", logger=None):
        self._log = logger or (lambda m: None)
        self._lang_tag = lang_tag
        self._engine = None
        self._error: Optional[str] = None

    @property
    def available(self) -> bool:
        return WINRT_OK and self._engine is not None

    def init(self) -> bool:
        if not WINRT_OK:
            self._error = "未安装 winrt 组件"
            return False
        try:
            self._engine = OcrEngine.try_create_from_language(Language(self._lang_tag))
            if self._engine is None:
                self._engine = OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                self._error = "系统没有安装可用的 OCR 语言包"
                return False
            tag = self._engine.recognizer_language.language_tag
            self._log(f"Windows OCR 就绪（{tag}）")
            return True
        except Exception as e:
            self._error = str(e)
            self._log(f"Windows OCR 初始化失败：{e}")
            return False

    # ---------- 识别 ----------

    def recognize(self, image: np.ndarray) -> list[dict]:
        """返回 [{text, box}]，box 是图像内的像素坐标。"""
        if self._engine is None and not self.init():
            return []
        try:
            return asyncio.run(self._recognize_async(image))
        except Exception as e:
            self._log(f"Windows OCR 识别异常：{e}")
            return []

    async def _recognize_async(self, image: np.ndarray) -> list[dict]:
        pil = Image.fromarray(image)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(buf.getvalue())
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await self._engine.recognize_async(bitmap)

        out = []
        for line in result.lines:
            words = list(line.words)
            text = tidy_text(line.text)
            if not text:
                continue
            if words:
                x1 = min(w.bounding_rect.x for w in words)
                y1 = min(w.bounding_rect.y for w in words)
                x2 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
                y2 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
                box = (int(x1), int(y1), int(x2), int(y2))
            else:
                box = (0, 0, 0, 0)
            out.append({"text": text, "box": box})
        return out
