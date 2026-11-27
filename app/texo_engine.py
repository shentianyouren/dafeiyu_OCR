"""本地公式识别（Texo，ONNX 版）。

只依赖 onnxruntime 和 numpy：不装 torch，不装 transformers，也不装 tokenizers。
模型三个文件共 77MB，随程序打包：

    models/texo/encoder_model.onnx   54MB   HGNetV2 骨干，输入 384x384
    models/texo/decoder_model.onnx   26MB   mBART 解码器，2 层 16 头，词表 687
    models/texo/tokenizer.json       17KB   词表，WordLevel，687 个 token

为什么不用 pix2tex：那套要 torch（装完 554MB），同一批图 19 张只对 10 张；
Texo 只要 onnxruntime，19 张全对。细节见 SPEC。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .paths import RES_DIR

MODEL_DIR = RES_DIR / "models" / "texo"

SIZE = 384
MEAN, STD = 0.7931, 0.1738
BOS, EOS, PAD = 0, 2, 1
MAX_LEN = 256


def _prepare(image: np.ndarray) -> np.ndarray:
    """把抓屏得到的 BGR 图整理成模型要的 [1, 3, 384, 384]。

    照搬官方预处理：裁掉白边 -> 长边缩到 384 以内 -> 补成 384x384 -> 归一化。
    补边用黑色，因为训练时就是这么补的，换成白色结果会崩。
    """
    if image.ndim == 3:
        rgb = image[:, :, ::-1]                      # BGR -> RGB
    else:
        rgb = np.repeat(image[:, :, None], 3, axis=2)

    img = Image.fromarray(np.ascontiguousarray(rgb)).convert("RGB")

    # 去白边：按灰度的最值归一化后取 <200 的像素做外接框
    gray = np.array(img.convert("L"), dtype=np.uint8)
    lo, hi = int(gray.min()), int(gray.max())
    if hi > lo:
        norm = (gray.astype(np.float32) - lo) / (hi - lo) * 255
        ys, xs = np.where(norm < 200)
        if len(xs):
            img = img.crop((int(xs.min()), int(ys.min()),
                            int(xs.max()) + 1, int(ys.max()) + 1))

    scale = SIZE / max(1, min(img.size))
    img = img.resize((max(1, round(img.width * scale)),
                      max(1, round(img.height * scale))), Image.BICUBIC)
    img.thumbnail((SIZE, SIZE), Image.BICUBIC)

    pad_w, pad_h = SIZE - img.width, SIZE - img.height
    canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    canvas.paste(img, (pad_w // 2, pad_h // 2))

    a = np.array(canvas.convert("L"), dtype=np.float32) / 255.0
    a = (a - MEAN) / STD
    a = np.repeat(a[None, None], 3, axis=1)
    return a.astype(np.float32)


class TexoEngine:
    """懒惰加载：不点开公式识别就不占内存。"""

    def __init__(self, logger=None):
        self._log = logger or (lambda m: None)
        self._enc = None
        self._dec = None
        self._vocab: list[str] = []
        self._cmds: set[str] = set()
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._tried = False

    # ---------- 加载 ----------

    @property
    def available(self) -> bool:
        return all((MODEL_DIR / n).exists() for n in
                   ("encoder_model.onnx", "decoder_model.onnx", "tokenizer.json"))

    def warmup(self) -> bool:
        with self._lock:
            if self._enc is not None:
                return True
            if self._tried and self._error:
                return False
            self._tried = True
            try:
                import onnxruntime as ort

                # 小图小模型，线程开满反而更慢，还会把整机 CPU 拉满
                threads = max(1, min(4, os.cpu_count() or 4))
                so = ort.SessionOptions()
                so.intra_op_num_threads = threads
                so.log_severity_level = 3

                t0 = time.time()
                self._enc = ort.InferenceSession(
                    str(MODEL_DIR / "encoder_model.onnx"), so,
                    providers=["CPUExecutionProvider"])
                self._dec = ort.InferenceSession(
                    str(MODEL_DIR / "decoder_model.onnx"), so,
                    providers=["CPUExecutionProvider"])

                data = json.loads((MODEL_DIR / "tokenizer.json").read_text(encoding="utf-8"))
                vocab = data["model"]["vocab"]                    # token -> id
                self._vocab = [""] * len(vocab)
                for tok, idx in vocab.items():
                    self._vocab[idx] = tok
                self._cmds = {t for t in vocab if t.startswith("\\")}

                self._log(f"公式引擎就绪（{threads} 线程，{time.time()-t0:.1f}s）")
                return True
            except Exception as e:
                self._error = str(e)
                self._log(f"公式引擎加载失败：{e}")
                return False

    # ---------- 识别 ----------

    def recognize(self, image: np.ndarray) -> tuple[str, float]:
        """返回 (LaTeX 文本, 耗时秒)。失败返回空串。"""
        if not self.warmup():
            return "", 0.0
        t0 = time.time()
        try:
            mem = self._enc.run(None, {"pixel_values": _prepare(image)})[0]
            ids = [BOS]
            for _ in range(MAX_LEN):
                logits = self._dec.run(None, {
                    "input_ids": np.array([ids], dtype=np.int64),
                    "encoder_hidden_states": mem,
                })[0]
                nxt = int(logits[0, -1].argmax())
                if nxt == EOS:
                    break
                ids.append(nxt)
            return self._detok(ids[1:]), time.time() - t0
        except Exception as e:
            self._log(f"公式识别异常：{e}")
            return "", time.time() - t0

    def _detok(self, ids: list[int]) -> str:
        """词表是 WordLevel，直接拼起来就是 LaTeX。

        不能简单加空格：m、c 之间加了空格会变成 "m c"；也不能全不加：
        \\int、u 会粘成 \\intu。所以先拼，再按词表把粘住的命令切开。
        """
        s = "".join(self._vocab[i] if 0 <= i < len(self._vocab) else "" for i in ids)
        s = "".join(ch for ch in s if not ch.isspace())

        out, i = [], 0
        while i < len(s):
            if s[i] == "\\":
                j = i + 1
                while j < len(s) and s[j].isalpha():
                    j += 1
                word = s[i:j]
                for k in range(len(word), 1, -1):
                    if word[:k] in self._cmds:
                        out.append(word[:k])
                        if word[k:]:
                            out.append(" ")
                            out.append(word[k:])
                        break
                else:
                    out.append(word)
                i = j
            else:
                out.append(s[i])
                i += 1

        # 模型词表里的私有写法映射回标准 LaTeX
        return ("".join(out)
                .replace("\\infin", "\\infty")
                .replace("\\bm", "\\boldsymbol"))


def looks_like_latex(s: str) -> bool:
    """粗判公式引擎的输出靠不靠谱，避免把垃圾塞给用户。"""
    t = (s or "").strip()
    if len(t) < 2:
        return False
    if t.count("{") != t.count("}"):
        return False
    # 至少要有一个反斜杠命令或等号，纯符号堆不算
    return t.count("\\") >= 1 or "=" in t
