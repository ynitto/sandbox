import ctypes
import sys
from ctypes import wintypes


def foreground_window():
    if sys.platform != "win32":
        return None
    foreground = ctypes.windll.user32.GetForegroundWindow
    foreground.restype = wintypes.HWND
    return foreground()


def activate_window(window):
    if sys.platform != "win32" or not window:
        return False
    if foreground_window() == window:
        return True
    return bool(ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(window)))


def paste_target():
    window = foreground_window()
    if not window:
        return None
    name = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(wintypes.HWND(window), name, len(name))
    if name.value in {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                      "Progman", "WorkerW", "#32768"}:
        return None
    return window


def modifiers_pressed():
    if sys.platform != "win32":
        return False
    get_state = ctypes.windll.user32.GetAsyncKeyState
    get_state.restype = wintypes.SHORT
    return any(get_state(key) < 0 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def send_paste(window):
    if not window or foreground_window() != window or sys.platform != "win32":
        raise RuntimeError("貼り付け先を確認できません。Ctrl+V で貼り付けてください。")

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("vk", wintypes.WORD), ("scan", wintypes.WORD),
            ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
            ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t),
        ]

    class InputData(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

    events = (Input * 4)(*(
        Input(1, InputData(keyboard=KeyboardInput(key, 0, flags, 0, 0)))
        for key, flags in ((0x11, 0), (ord("V"), 0), (ord("V"), 2), (0x11, 2))
    ))
    send = ctypes.windll.user32.SendInput
    send.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    send.restype = wintypes.UINT
    if send(len(events), events, ctypes.sizeof(Input)) != len(events):
        raise RuntimeError("キー入力を送信できません。Ctrl+V で貼り付けてください。")
