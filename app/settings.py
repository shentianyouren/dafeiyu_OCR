"""设置窗。改完立刻生效，不需要重启。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

STYLE = """
QWidget { background: #FFFFFF; color: #2C2C2A; font-size: 13px; }
QLabel#h { font-size: 14px; font-weight: 500; color: #2C2C2A; }
QLabel#tip { font-size: 11px; color: #888780; }
QLineEdit, QComboBox {
    background: #FAFAF8; border: 1px solid #D3D1C7; border-radius: 7px;
    padding: 5px 8px; font-size: 12px; min-height: 20px;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #378ADD; }
QPushButton {
    background: #F1EFE8; border: 1px solid #D3D1C7; border-radius: 7px;
    padding: 6px 16px; font-size: 12px;
}
QPushButton:hover { background: #E6E4DC; }
QPushButton#primary { background: #185FA5; border-color: #185FA5; color: #FFFFFF; }
QPushButton#primary:hover { background: #0C447C; }
QSlider::groove:horizontal { height: 4px; background: #E6E4DC; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #185FA5; width: 14px; margin: -5px 0; border-radius: 7px;
}
QFrame#sep { background: #E6E4DC; max-height: 1px; border: none; }
"""


class SettingsWindow(QWidget):
    saved = Signal(dict)

    def __init__(self, cfg: dict):
        super().__init__(
            None,
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setWindowTitle("识屏识字 · 设置")
        self.setStyleSheet(STYLE)
        self._cfg = cfg

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        root.addWidget(_head("识别"))

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.cmb_engine = QComboBox()
        self.cmb_engine.addItem("智能（文字走系统 OCR，认出公式自动转 LaTeX）", "local")
        self.cmb_engine.addItem("只识别公式（Texo，离线约 0.2 秒）", "formula")
        _select(self.cmb_engine, cfg.get("engine", "local"))
        form.addRow("识别方式", self.cmb_engine)

        self.cmb_text = QComboBox()
        self.cmb_text.addItem("极速（系统引擎，约 0.05 秒）", "fast")
        if _has_rapidocr():
            self.cmb_text.addItem("增强（RapidOCR，约 1.5 秒，手写更强）", "accurate")
            _select(self.cmb_text, cfg.get("text_engine", "fast"))
        else:
            # 精简版没打包 RapidOCR，干脆不摆这个选项，免得选了没反应
            _select(self.cmb_text, "fast")
            self.cmb_text.setToolTip("精简版只带系统引擎；需要手写识别请用完整版")
        form.addRow("文字引擎", self.cmb_text)

        self.chk_preview = QCheckBox("识别后弹出预览窗（可改错字再复制）")
        self.chk_preview.setChecked(bool(cfg.get("show_preview", True)))
        form.addRow("", self.chk_preview)

        root.addLayout(form)

        ftip = QLabel("全部离线运行，不需要联网，也不上传任何截图。公式识别由内置的 Texo 模型完成，"
                      "只针对英文印刷体公式；中文公式请用文字模式。")
        ftip.setObjectName("tip")
        ftip.setWordWrap(True)
        root.addWidget(ftip)

        root.addWidget(_sep())
        root.addWidget(_head("操作"))

        oform = QFormLayout()
        oform.setSpacing(9)
        self.ed_hotkey = QLineEdit(cfg.get("hotkey", "ctrl+shift+o"))
        self.ed_hotkey.setPlaceholderText("ctrl+shift+o")
        oform.addRow("全局快捷键", self.ed_hotkey)

        srow = QHBoxLayout()
        self.sl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_opacity.setRange(30, 100)
        self.sl_opacity.setValue(int(float(cfg.get("ball_opacity", 0.92)) * 100))
        self.lbl_opacity = QLabel(f"{self.sl_opacity.value()}%")
        self.lbl_opacity.setObjectName("tip")
        self.sl_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity.setText(f"{v}%"))
        srow.addWidget(self.sl_opacity, 1)
        srow.addWidget(self.lbl_opacity)
        oform.addRow("悬浮球不透明度", srow)

        root.addLayout(oform)
        root.addStretch(1)

        foot = QHBoxLayout()
        self.lbl_msg = QLabel("")
        self.lbl_msg.setObjectName("tip")
        btn_cancel = QPushButton("关闭")
        btn_cancel.clicked.connect(self.hide)
        btn_save = QPushButton("保存")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save)
        foot.addWidget(self.lbl_msg, 1)
        foot.addWidget(btn_cancel)
        foot.addWidget(btn_save)
        root.addLayout(foot)

        self.resize(480, 472)

    def _save(self) -> None:
        cfg = {
            "engine": self.cmb_engine.currentData(),
            "show_preview": self.chk_preview.isChecked(),
            "hotkey": self.ed_hotkey.text().strip() or "ctrl+shift+o",
            "ball_opacity": self.sl_opacity.value() / 100.0,
            "text_engine": self.cmb_text.currentData(),
        }
        self.lbl_msg.setText("已保存")
        self.saved.emit(cfg)


def _head(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("h")
    return lb


def _sep() -> QFrame:
    f = QFrame()
    f.setObjectName("sep")
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def _select(combo: QComboBox, value) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)


def _has_rapidocr() -> bool:
    """当前这份程序里有没有打包 RapidOCR（精简版没有）。"""
    try:
        import importlib.util

        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:
        return False
