"""环境自检。

最重要的作用：验证「抓屏到底能不能抓到画面」。
开着视频（B站、YouTube 都行）再跑一次，如果这里存出来的截图不是黑的，
说明 DXGI 这条路径在你机器上通了 —— 这是整个软件最关键的一环。

用法：
    .venv\\Scripts\\python.exe test_env.py
    .venv\\Scripts\\python.exe test_env.py --ocr     # 额外跑一次真实文字识别
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import paths  # noqa: E402  必须最先导入

import numpy as np  # noqa: E402

OK = "[ 通过 ]"
NO = "[ 失败 ]"
WARN = "[ 注意 ]"


def line(t=""):
    print(t)


def check_imports():
    line("— 依赖检查 —")
    mods = [
        ("PySide6", "界面"),
        ("dxcam", "DXGI 抓屏"),
        ("rapidocr_onnxruntime", "增强文字引擎"),
        ("onnxruntime", "推理运行时"),
        ("PIL", "图像处理"),
        ("numpy", "数值计算"),
    ]
    all_ok = True
    for name, desc in mods:
        try:
            m = __import__(name)
            ver = getattr(m, "__version__", "?")
            line(f"  {OK} {name:20s} {ver:14s} {desc}")
        except Exception as e:
            all_ok = False
            line(f"  {NO} {name:20s} {'':14s} {desc}  ({e})")
    return all_ok


def check_paths():
    line()
    line("— 存储位置 —")
    line(f"  程序目录     {paths.ROOT}")
    line(f"  配置         {paths.CONFIG_FILE}")
    line(f"  日志目录     {paths.LOGS_DIR}")
    flag = "（在 C 盘）" if str(paths.ROOT).upper().startswith("C:") else "（不在 C 盘）"
    line(f"  {OK if 'C:' not in str(paths.ROOT).upper() else WARN} 数据存放在 {flag}")


def check_displays():
    line()
    line("— 显示器 —")
    try:
        import dxcam
        info = dxcam.output_info()
        for row in str(info).splitlines():
            if row.strip():
                line(f"  {row.strip()}")
        devices = str(dxcam.device_info())
        line(f"  {devices.strip()}")
    except Exception as e:
        line(f"  {NO} 无法枚举显示器：{e}")


def check_capture(save_dir: Path):
    line()
    line("— 抓屏测试 —")
    from app.capture import ScreenGrabber, primary_screen_rect

    log = lambda m: line(f"  {m}")  # noqa: E731
    grabber = ScreenGrabber(log)
    line(f"  使用后端：{grabber.backend}")

    pr = primary_screen_rect()
    # 取屏幕中间一块 900x560，够容纳一段文字和公式
    w, h = 900, 560
    cx, cy = (pr[0] + pr[2]) // 2, (pr[1] + pr[3]) // 2
    rect = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
    line(f"  抓取区域：{rect}")

    try:
        res = grabber.grab(rect)
    except Exception as e:
        line(f"  {NO} 抓取异常：{e}")
        return False

    img = res.image
    std = float(img.std())
    mean = float(img.mean())
    line(f"  图像尺寸：{img.shape[1]} x {img.shape[0]}   标准差 {std:.1f}   均值 {mean:.1f}")

    out = save_dir / "selftest_capture.png"
    try:
        from PIL import Image
        Image.fromarray(img).save(out)
        line(f"  样本已保存：{out}")
    except Exception as e:
        line(f"  {WARN} 样本保存失败：{e}")

    grabber.close()

    if std < 1.0 and mean < 6.0:
        line(f"  {NO} 画面是全黑的 —— 当前桌面可能没有内容，或抓屏被拦截")
        line("      开着视频再跑一次；如果还是黑的，说明该播放器有 DRM 保护")
        return False

    line(f"  {OK} 抓到了真实画面，DXGI 路径可用")
    return True


def check_ocr():
    line()
    line("— OCR 冒烟测试 —")
    from PIL import Image, ImageDraw, ImageFont

    from app.ocr_engine import LocalEngine

    img = Image.new("RGB", (760, 150), "white")
    d = ImageDraw.Draw(img)
    # 必须用真字体。PIL 默认位图字体不含中文字形，会把中文画成豆腐块，
    # 结果 OCR 认不出来，还误判成"引擎失手"。
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:
        font = ImageFont.load_default()
    d.text((24, 26), "Hello OCR 12345", font=font, fill="black")
    d.text((24, 78), "识屏识字测试 test", font=font, fill="black")
    arr = np.array(img)

    engine = LocalEngine(logger=lambda m: line(f"  {m}"))
    res = engine.recognize(arr)
    line(f"  耗时 {res.elapsed:.2f}s")
    line(f"  识别结果：{res.plain!r}")
    if res.plain.strip():
        line(f"  {OK} 本地识别链路打通")
        return True
    line(f"  {NO} 没有识别出文字，查看 logs/app.log 排查")
    return False


def main():
    save_dir = paths.LOGS_DIR
    line("=" * 62)
    line("识屏识字 · 环境自检")
    line(f"Python {sys.version.split()[0]}  |  {sys.executable}")
    line("=" * 62)

    deps_ok = check_imports()
    check_paths()
    if deps_ok:
        check_displays()
        cap_ok = check_capture(save_dir)
    else:
        cap_ok = False

    ocr_ok = None
    if "--ocr" in sys.argv:
        ocr_ok = check_ocr()

    line()
    line("=" * 62)
    line(f"依赖 {'正常' if deps_ok else '缺失'}  |  抓屏 {'可用' if cap_ok else '异常'}"
         + (f"  |  识别 {'可用' if ocr_ok else '异常'}" if ocr_ok is not None else ""))
    line("=" * 62)
    return 0 if deps_ok and cap_ok else 1


if __name__ == "__main__":
    sys.exit(main())
