"""用一张图片测试文字识别效果。

    python tools\\sample_test.py                     # 测默认样本
    python tools\\sample_test.py 图片路径
    python tools\\sample_test.py 图片路径 --accurate  # 走 RapidOCR 增强引擎
"""

import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from app import paths  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.ocr_engine import LocalEngine  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    accurate = "--accurate" in sys.argv

    img_path = Path(args[0]) if args else (paths.LOGS_DIR / "selftest_capture.png")
    if not img_path.exists():
        print("图片不存在:", img_path)
        return 1

    img = np.array(Image.open(img_path).convert("RGB"))
    print("图片:", img_path)
    print("尺寸:", img.shape, flush=True)

    engine = LocalEngine(
        text_engine="accurate" if accurate else "fast",
        logger=lambda m: print("  [引擎]", m, flush=True),
    )

    t_warm = time.time()
    engine.warmup()
    print(f"预热完成，耗时 {time.time() - t_warm:.2f}s", flush=True)

    res = engine.recognize(img)

    print("-" * 60)
    print(f"引擎={res.detail}  耗时={res.elapsed:.3f}s  字符数={len(res.plain)}")
    print("-" * 60)
    print(res.plain or "(空)")
    print("-" * 60)
    if res.warning:
        print("提示:", res.warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
