import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

HOTKEY_ID = 1
WM_HOTKEY = 0x0312


class Hotkey(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = False

    def register(self):
        if sys.platform != "win32":
            return False
        modifiers = 0x0002 | 0x0004 | 0x4000  # Ctrl, Shift, no repeat
        self.registered = bool(ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_ID, modifiers, ord("V")
        ))
        return self.registered

    def close(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_dispatcher_MSG":
            event = wintypes.MSG.from_address(int(message))
            if event.message == WM_HOTKEY and event.wParam == HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0
