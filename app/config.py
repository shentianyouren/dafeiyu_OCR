"""用户配置的读写。配置存在项目根的 config.json，删掉就恢复默认。"""

import json
from copy import deepcopy

from .paths import CONFIG_FILE

DEFAULTS = {
    # 全局快捷键，格式 ctrl+shift+o / alt+q
    "hotkey": "ctrl+shift+o",
    # 识别方式：local 文字优先、遇到公式自动转 LaTeX / formula 只识别公式
    "engine": "local",
    # 文字引擎：fast=系统OCR（约50ms）/ accurate=RapidOCR（约1.5s，手写更强）
    # 精简版没有 RapidOCR，选了 accurate 会自动退回系统 OCR
    "text_engine": "fast",
    # 识别完是否弹出预览窗
    "show_preview": True,
    # 悬浮球透明度 0.3 ~ 1.0
    "ball_opacity": 0.92,
    # 悬浮球位置 {"x": 100, "y": 100}，None 表示首次启动放到屏幕右侧中部
    "ball_position": None,
    # 悬浮球直径
    "ball_size": 52,
}


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    if not CONFIG_FILE.exists():
        save(DEFAULTS)
        return deepcopy(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置不是对象")
        return _merge(DEFAULTS, raw)
    except Exception:
        # 配置坏了就用默认值，不要让软件起不来
        return deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update(**kwargs) -> dict:
    cfg = load()
    cfg = _merge(cfg, kwargs)
    save(cfg)
    return cfg
