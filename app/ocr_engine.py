"""本地识别引擎（完全离线）。

三条通道，同一张图按需组合，最后仲裁用哪个结果：

  Windows 系统 OCR —— 约 50 毫秒，印刷体很准，默认走这条。
  RapidOCR（ONNX） —— 约 1.5 秒，手写和复杂排版更能扛。仅完整版带，系统 OCR
                     漏字时兜底；精简版没有这段代码也不会报错。
  Texo（ONNX，77MB）—— 约 0.15 秒，把公式读成 LaTeX。

不联网，不上传截图。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# 出现这些符号，说明框到的内容里混着公式
MATH_HINT = set("∫∑∏√±×÷≤≥≈≠≡∂∇∞αβγδεζηθλμνξπρστφχψωΔΣΠΩ∈∉⊂⊃∪∩→←↔⇒⇔⋅⋯·")


@dataclass
class Block:
    text: str
    box: tuple[int, int, int, int]
    score: float = 1.0

    @property
    def y_center(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def x_left(self) -> int:
        return self.box[0]


@dataclass
class OcrResult:
    plain: str
    markdown: str
    blocks: list[Block] = field(default_factory=list)
    engine: str = "local"
    detail: str = ""
    elapsed: float = 0.0
    warning: Optional[str] = None
    has_formula: bool = False
    latex: str = ""


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _join_text(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    return a + " " + b


def suspect_formula(text: str) -> bool:
    """粗略判断识别结果里是不是混着没解析好的公式。

    只用来给用户一句提示（"这段像公式，可以点重识走大模型"），
    不参与任何识别决策，所以宁可宽松一点。
    """
    t = (text or "").strip()
    if len(t) < 2:
        return False
    math = sum(1 for c in t if c in MATH_HINT)
    struct = sum(1 for c in t if c in "^_{}=<>\\")
    return math >= 2 or struct >= 4


class LocalEngine:
    def __init__(self, text_engine: str = "fast", logger=None, formula=None):
        self._log = logger or (lambda m: None)
        self.formula = formula              # TexoEngine，可为 None
        self.text_engine = text_engine      # fast=系统OCR  accurate=RapidOCR
        self._win = None
        self._rapid = None
        self._rapid_missing = False         # 精简版没打包 RapidOCR，探测到就记住
        self._lock = threading.Lock()
        self._rapid_lock = threading.Lock()
        self._ready = False
        self._init_error: Optional[str] = None

    # ---------- 加载 ----------

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def warmup(self) -> bool:
        with self._lock:
            if self._ready:
                return True
            try:
                from .win_ocr import WindowsOcr

                self._win = WindowsOcr(logger=self._log)
                if self._win.init():
                    self._ready = True
                    return True
                self._log("系统 OCR 不可用，改用 RapidOCR")
            except Exception as e:
                self._log(f"系统 OCR 初始化异常：{e}")

            try:
                self._get_rapid()
                self._ready = True
                self._log("RapidOCR 就绪")
                return True
            except Exception as e:
                self._init_error = str(e)
                self._log(f"文字引擎全部不可用：{e}")
                return False

    def _get_rapid(self):
        if self._rapid is not None:
            return self._rapid
        if self._rapid_missing:
            return None                      # 精简版没有 RapidOCR，别每次都试一遍
        with self._rapid_lock:
            if self._rapid is not None:
                return self._rapid
            self._rapid = self._build_rapid()
        return self._rapid

    def _build_rapid(self):
        # onnxruntime 默认按 CPU 核心数开线程。实测在一台 24 核机器上，
        # 全开反而比 4 线程慢近 4 倍（3.87s vs 1.01s）：识别的是小图，
        # 线程调度和同步开销远超并行收益，还会把 CPU 瞬间拉满，导致整机卡顿。
        threads = max(1, min(4, os.cpu_count() or 4))
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as e:
            self._rapid_missing = True
            raise RuntimeError(f"RapidOCR 不可用：{e}") from e

        for kwargs in ({"intra_op_num_threads": threads}, {}):
            try:
                eng = RapidOCR(**kwargs)
                self._log(f"RapidOCR 就绪（{kwargs.get('intra_op_num_threads', '默认')} 线程）")
                return eng
            except TypeError:
                continue          # 老版本不认这个参数，退回默认
            except Exception as e:
                self._log(f"RapidOCR 初始化失败：{e}")
                break
        return RapidOCR()

    # ---------- 识别 ----------

    def recognize(self, image: np.ndarray,
                  engine_override: str | None = None) -> OcrResult:
        t0 = time.time()
        if not self._ready and not self.warmup():
            return OcrResult("", "", engine="local", elapsed=time.time() - t0,
                             warning=f"识别引擎未就绪：{self._init_error}")

        # 先跑系统 OCR：它只要 10 毫秒，顺便能立刻告诉我们图里有没有汉字。
        blocks = self._run_win(image)
        source = "win"
        if not blocks and engine_override != "accurate" and not self._rapid_missing:
            self._log("系统 OCR 没认出内容，改用 RapidOCR")
            blocks = self._run_rapid(image)
            source = "rapid"

        # 公式通道。判据不依赖"文字认没认出来"（系统 OCR 会把公式读成散字），
        # 只用一个确定能判的：出现 4 个以上汉字就跳过——Texo 词表里没有汉字，
        # 中文段落上白跑一遍要 1~2 秒，还容易吐出一堆乱码。
        latex = ""
        if self.formula is not None and not _has_cjk(_compose(blocks)[0]):
            latex, cost = self.formula.recognize(image)
            if latex:
                self._log(f"公式通道 {cost:.2f}s：{latex[:50]}")

        plain, markdown = _compose(blocks)

        # 两条结果都拿到之后再决定用哪个
        if latex and _looks_ok(latex) and _prefer_formula(plain, latex):
            return OcrResult(plain=latex, markdown=latex, blocks=blocks,
                             engine="formula", detail="Texo 公式",
                             elapsed=time.time() - t0, has_formula=True, latex=latex)

        # 系统 OCR 认得少、残留多时，再花 1~3 秒让 RapidOCR 补一遍（完整版才有）
        blocks, source = self._maybe_rapid(image, blocks, source, engine_override)
        plain, markdown = _compose(blocks)
        detail = {"win": "系统OCR", "rapid": "RapidOCR"}.get(source, source)
        return OcrResult(plain=plain, markdown=markdown, blocks=blocks,
                         engine="local", detail=detail,
                         elapsed=time.time() - t0)

    def _maybe_rapid(self, image: np.ndarray, blocks: list[Block],
                     source: str, override: str | None) -> tuple[list[Block], str]:
        """系统 OCR 认得少、残留多的时候，再跑一遍 RapidOCR。

        残留墨迹多本身不能证明漏字——表格线、图例、下划线、图标都会留着墨迹。
        只有「残留多 且 认出的字很少」才值得再跑一遍，否则每次框选都双份识别。
        精简版没有打进 RapidOCR，第一次探测到缺失后就一直跳过。
        """
        if (override or self.text_engine) == "accurate" and not self._rapid_missing:
            return self._run_rapid(image), "rapid"
        if source != "win" or not blocks or self._rapid_missing:
            return blocks, source

        chars = sum(len(b.text) for b in blocks)
        uncovered = _uncovered_ratio(blocks, image)
        if image.shape[0] >= 120 and uncovered > 0.45 and chars < 60:
            alt = self._run_rapid(image)
            if len(alt) > len(blocks):
                self._log(f"系统OCR 只认出 {chars} 字、残留 {uncovered:.0%}，"
                          f"RapidOCR 补到 {len(alt)} 行")
                return alt, "rapid"
        return blocks, "win"

    def recognize_formula(self, image: np.ndarray) -> OcrResult:
        """只走公式通道，用于「只识别公式」模式和预览窗的「重识」。

        这里不跑系统 OCR，也不做仲裁——用户已经明确要公式了，再拿文字
        结果去覆盖他，反而会把他框的那行公式冲掉。
        """
        t0 = time.time()
        if self.formula is None:
            return OcrResult("", "", engine="local", elapsed=0.0,
                             warning="公式模型没加载成功：models/texo 缺失或损坏，"
                                     "重装完整版即可。")
        latex, cost = self.formula.recognize(image)
        if not latex:
            return OcrResult("", "", engine="formula", elapsed=time.time() - t0,
                             warning="没认出公式。把框贴紧公式边缘，或换回「智能」模式。")
        if not _looks_ok(latex):
            return OcrResult("", "", engine="formula", elapsed=time.time() - t0,
                             warning="公式这次输出不完整，重新框选一次通常就好了。")
        self._log(f"公式识别 {cost:.2f}s：{latex[:60]}")
        return OcrResult(plain=latex, markdown=latex, engine="formula",
                         detail="Texo 公式", elapsed=time.time() - t0,
                         has_formula=True, latex=latex)

    def _run_rapid(self, image: np.ndarray) -> list[Block]:
        try:
            eng = self._get_rapid()
            if eng is None:
                return []
            result, _elapse = eng(image)
        except Exception as e:
            self._log(f"RapidOCR 推理异常：{e}")
            return []

        blocks: list[Block] = []
        for item in result or []:
            try:
                points, text, score = item[0], item[1], item[2]
                arr = np.array(points).reshape(-1, 2)
                box = (int(arr[:, 0].min()), int(arr[:, 1].min()),
                       int(arr[:, 0].max()), int(arr[:, 1].max()))
                if str(text).strip():
                    blocks.append(Block(str(text).strip(), box, float(score)))
            except Exception:
                continue
        return blocks

    def _run_win(self, image: np.ndarray) -> list[Block]:
        if self._win is None or not self._win.available:
            return []
        rows = self._win.recognize(image)
        return [Block(r["text"], r["box"], 1.0) for r in rows]

# ---------- 工具 ----------


def _uncovered_ratio(blocks: list[Block], image: np.ndarray) -> float:
    """识别框之外还残留多少内容像素。

    比"识别面积占比"可靠：像 y=|x|= 这种纯符号行，系统 OCR 会直接跳过，
    但它在图里的面积本身就不大，用面积比判断会漏过去。
    这里反过来数「没被任何识别框盖住的字迹像素」占全部内容像素的比例。
    """
    try:
        if image.ndim == 3:
            gray = image.mean(axis=2)
        else:
            gray = image.astype(np.float32)
        ink = gray < 200
        total = int(ink.sum())
        if total < 50:
            return 0.0
        covered = np.zeros_like(ink)
        h, w = ink.shape
        for b in blocks:
            x0, y0, x1, y1 = b.box
            covered[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = True
        return float((ink & ~covered).sum()) / total
    except Exception:
        return 0.0


def _compose(blocks: list[Block]) -> tuple[str, str]:
    """按行聚合，再按从左到右拼成段落。"""
    if not blocks:
        return "", ""

    heights = [max(1, b.box[3] - b.box[1]) for b in blocks]
    median_h = float(np.median(heights)) if heights else 16.0
    tol = max(6.0, median_h * 0.6)

    ordered = sorted(blocks, key=lambda b: (b.y_center, b.x_left))
    rows: list[list[Block]] = []
    for b in ordered:
        if rows and abs(b.y_center - np.mean([x.y_center for x in rows[-1]])) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])

    lines = []
    for row in rows:
        row.sort(key=lambda b: b.x_left)
        line = ""
        for b in row:
            line = _join_text(line, b.text)
        lines.append(line)

    text = "\n".join(lines).strip()
    return text, text


def _prefer_formula(plain: str, latex: str) -> bool:
    """文字结果和公式结果都在手上时，选哪个。

    判据按可靠性排序：中文一票否决（公式里不会有汉字），
    再看 LaTeX 有没有结构命令、文字那边是不是只剩几个散字。
    """
    import re

    if sum(1 for c in plain if _is_cjk(c)) >= 4:
        return False
    if not plain.strip():
        return True

    struct = len(re.findall(
        r"\\frac|\\sqrt|\\sum|\\int|\\prod|\\lim|\\left|\\begin|"
        r"\\pm|\\infty|\\partial|\\nabla|\\theta|\\sigma|\\alpha|"
        r"\^\{|_\{|=", latex))
    # 公式好歹得有个关系符或运算符。像表格里一排数字，模型也会硬套
    # \overbrace{0\qquad 60\qquad ...} 这种壳子，看着像 LaTeX，其实没信息。
    has_op = bool(re.search(
        r"[=+<>]|\-|\\times|\\div|\\pm|\\cdot|\\le|\\ge|\\ne|"
        r"\\approx|\\equiv|\\frac|\\sqrt|\\sum|\\int|\\infty|\^|_", latex))
    letters = sum(1 for c in plain if c.isalnum())
    return struct >= 2 or (letters <= 6 and has_op)


def _has_cjk(text: str, need: int = 4) -> bool:
    """图里是不是中文内容。汉字数达到阈值就当作文字，别再走公式模型。"""
    n = 0
    for c in text or "":
        if _is_cjk(c):
            n += 1
            if n >= need:
                return True
    return False


def _looks_ok(latex: str) -> bool:
    """公式输出先做一遍语法体检，免得把半截垃圾顶掉正常文字。"""
    import re

    t = (latex or "").strip()
    if len(t) < 2 or t.count("{") != t.count("}"):
        return False
    # 自回归解码偶尔会陷进重复循环，吐出 1.18484848888888888 这种尾巴
    if re.search(r"(.)\1{11,}", t):
        return False
    # 数字占绝对多数，基本是失败输出的特征，真正的公式不会这样
    digits = sum(1 for c in t if c.isdigit())
    if len(t) > 20 and digits / len(t) > 0.7:
        return False
    return t.count("\\") >= 1 or "=" in t
