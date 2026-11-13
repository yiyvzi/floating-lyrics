import ctypes
from ctypes import wintypes
import re

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtGui import QWindow

WM_HOTKEY = 0x0312
MODIFIERS = {"ALT": 1, "CTRL": 2, "SHIFT": 4, "WIN": 8}


def parse_shortcut(shortcut):
    *modifiers, key = shortcut.upper().replace(" ", "").split("+")
    if not modifiers or len(set(modifiers)) != len(modifiers):
        raise ValueError("快捷键需要至少一个修饰键，例如 Ctrl+Alt+F10")
    if any(modifier not in MODIFIERS for modifier in modifiers):
        raise ValueError("支持 Ctrl、Alt、Shift 和 Win 修饰键")
    if re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key):
        virtual_key = 0x6F + int(key[1:])
    elif re.fullmatch(r"[A-Z0-9]", key):
        virtual_key = ord(key)
    else:
        raise ValueError("快捷键主键须为字母、数字或 F1 到 F24")
    return sum(MODIFIERS[modifier] for modifier in modifiers) | 0x4000, virtual_key


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, application):
        super().__init__()
        self.application = application
        self.callbacks = {}
        # Keep the shortcut target independent of the overlay's visibility and styles.
        self.target = QWindow()
        self.target.create()
        self.hwnd = int(self.target.winId())
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = (
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
        )
        self.user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        application.installNativeEventFilter(self)
        application.aboutToQuit.connect(self.close)

    def register(self, identifier, shortcut, callback):
        modifiers, key = parse_shortcut(shortcut)
        if not self.user32.RegisterHotKey(self.hwnd, identifier, modifiers, key):
            raise OSError(ctypes.get_last_error(), "Windows 无法注册此快捷键")
        self.callbacks[identifier] = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            event = wintypes.MSG.from_address(int(message))
            if (event.hWnd == self.hwnd and event.message == WM_HOTKEY
                    and event.wParam in self.callbacks):
                self.callbacks[event.wParam]()
                return True, 0
        return False, 0

    def close(self):
        for identifier in self.callbacks:
            self.user32.UnregisterHotKey(self.hwnd, identifier)
        self.callbacks.clear()
        self.application.removeNativeEventFilter(self)

