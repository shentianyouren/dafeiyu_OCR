# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    .venv-build\\Scripts\\python.exe -m PyInstaller build.spec --noconfirm

产物：dist\\识屏识字.exe（单文件，免安装，双击即用）
"""

import os

from PyInstaller.utils.hooks import collect_data_files

# SANS_LEAN=1 → 精简版：不带 RapidOCR（连同它依赖的 opencv，约 63MB）。
LEAN = os.environ.get("SANS_LEAN") == "1"

# RapidOCR 的 ONNX 模型和 config.yaml 在包目录里，必须一起打进去，
# 否则运行时报「找不到模型」，而这台机器上装了包所以看不出问题。
datas = [] if LEAN else collect_data_files("rapidocr_onnxruntime")

# 悬浮球素材（人物 + 气泡）和程序图标
datas += [
    ("assets/person.png", "assets"),
    ("assets/bubble.png", "assets"),
    ("assets/icon.ico", "assets"),
]

# 公式模型（Texo，ONNX）：77MB，必须整个目录带进去，
# 少一个文件公式通道就会静默失效。
datas += [
    ("models/texo/encoder_model.onnx", "models/texo"),
    ("models/texo/decoder_model.onnx", "models/texo"),
    ("models/texo/tokenizer.json", "models/texo"),
]

hiddenimports = [
    # dxcam 的颜色转换靠这个 Cython 扩展：包里没有 opencv，走的就是它。
    # 它是 import_module 动态加载的，PyInstaller 静态分析看不到。
    "dxcam.processor._numpy_kernels",
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.globalization",
]

# 这些都用不到，排除掉能省一半体积
excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3", "pdb",
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook",
    # 公式模型走 ONNX 推理，这些深度学习框架一个都不需要
    "torch", "transformers", "tokenizers", "sentencepiece", "accelerate",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtUiTools", "PySide6.QtXml",
]

if LEAN:
    # 精简版不带 opencv / RapidOCR，dxcam 走自带的 Cython 内核做颜色转换
    excludes += ["rapidocr_onnxruntime", "cv2", "opencv_python"]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="识屏识字-精简" if LEAN else "识屏识字",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
