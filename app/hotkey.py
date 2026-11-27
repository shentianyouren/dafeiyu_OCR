"""全局快捷键。

用 Win32 的 RegisterHotKey 而不是 keyboard / pynput：
不需要管理员权限，不装键盘钩子，不会杀软报警，也不会拖慢按键响应。
消息通过 Qt 的 nativeEventFilter 回到界面线程。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_HOTKEY_ID = 0xB001

_MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C,
}


def parse_hotkey(text: str) -> Optional[tuple[int, int]]:
    """'ctrl+shift+o' -> (modifiers, vk)。无法解析返回 None。"""
    if not text:
        return None
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    vk = None
    for part in parts:
        if part in _MOD_MAP:
            mods |= _MOD_MAP[part]
            continue
        if part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
            continue
        if len(part) == 1:
            ch = part.upper()
            if "A" <= ch <= "Z" or "0" <= ch <= "9":
                vk = ord(ch)
                continue
        if part.startswith("f") and part[1:].isdigit():
            n = int(part[1:])
            if 1 <= n <= 24:
                vk = 0x6F + n
                continue
        return None

    if vk is None or mods == 0:
        return None
    return mods | MOD_NOREPEAT, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        try:
            et = event_type.decode() if isinstance(event_type, (bytes, bytearray)) else str(event_type)
        except Exception:
            et = ""
        if "windows" in et.lower():
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and int(msg.wParam) == _HOTKEY_ID:
                    self._on_hotkey()
                    return True, 0
            except Exception:
                pass
        return False, 0


class HotkeyManager(QObject):
    def __init__(self, app, callback, logger=None):
        super().__init__()
        self._app = app
        self._log = logger or (lambda m: None)
        self._registered = False
        self._filter = _Filter(callback)
        self._user32 = ctypes.windll.user32
        app.installNativeEventFilter(self._filter)

    def register(self, text: str) -> tuple[bool, str]:
        self.unregister()
        parsed = parse_hotkey(text)
        if not parsed:
            return False, f"快捷键格式无法识别：{text}（示例：ctrl+shift+o）"
        mods, vk = parsed
        if not self._user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
            err = ctypes.get_last_error()
            return False, f"快捷键 {text} 注册失败，可能已被其他软件占用（错误码 {err}）"
        self._registered = True
        self._log(f"全局快捷键已注册：{text}")
        return True, ""

    def unregister(self) -> None:
        if self._registered:
            try:
                self._user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass
            self._registered = False
