"""路径与运行环境。

开发时，一切都在项目目录里。
打包成 exe 后，配置和日志写在 exe 旁边（绿色版，拷走即用）；
万一 exe 被放在只读位置，自动退到 %LOCALAPPDATA%\\SanshiOcr。

不再需要下载模型：文字识别走 Windows 系统 OCR，
兜底用的 RapidOCR 轻量模型（约 15MB）随程序一起打包。
"""

import os
import sys
from pathlib import Path


def _exe_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


ROOT = _exe_root()
if not _writable(ROOT):
    ROOT = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "SanshiOcr"
    ROOT.mkdir(parents=True, exist_ok=True)

APP_DIR = ROOT / "app"
CONFIG_FILE = ROOT / "config.json"
LOGS_DIR = ROOT / "logs"

# 只读资源（图标之类）跟着程序走。打包后它们在解压目录里，
# 不在 exe 旁边，所以不能拿 ROOT 去拼。
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ASSETS_DIR = RES_DIR / "assets"

try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
