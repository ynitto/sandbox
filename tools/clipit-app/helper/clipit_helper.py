"""JSON command-line helper for ClipIt on Windows."""

import asyncio
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path


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


class OcrUnavailable(RuntimeError):
    pass


def recognize(path):
    return asyncio.run(_recognize(Path(path).resolve()))


async def _recognize(path):
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    except ImportError as error:
        raise OcrUnavailable(
            "winrt が導入されていません。OCR を有効にするには、Windows で "
            "python -m pip install -r requirements-ocr.txt を実行してください。"
        ) from error

    engines = []
    for tag in ("ja-JP", "en-US"):
        language = Language(tag)
        if OcrEngine.is_language_supported(language):
            engine = OcrEngine.try_create_from_language(language)
            if engine is not None:
                engines.append(engine)
    if not engines:
        raise OcrUnavailable("Windows の言語設定で日本語または英語の OCR 対応言語パックを追加してください。")

    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        try:
            converted = SoftwareBitmap.convert(bitmap, BitmapPixelFormat.BGRA8)
            try:
                for engine in engines:
                    result = await engine.recognize_async(converted)
                    if result.text.strip():
                        return result.text
            finally:
                converted.close()
        finally:
            bitmap.close()
    finally:
        stream.close()
    raise ValueError("画像から文字を検出できませんでした。")


def dispatch(command, request):
    if command == "get-foreground":
        return {"window": paste_target()}

    if command == "paste":
        window = request.get("window")
        if type(window) is not int or not 0 < window < 1 << (8 * ctypes.sizeof(wintypes.HWND)):
            raise ValueError("window には有効な HWND の整数値を指定してください。")
        if not activate_window(window):
            raise RuntimeError("貼り付け先を前面にできません。Ctrl+V で貼り付けてください。")
        send_paste(window)
        return {}

    path = request.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("path には画像ファイルのパスを指定してください。")
    return {"text": recognize(path)}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("サブコマンドを1つ指定してください。")
        command = sys.argv[1]
        if command not in ("get-foreground", "paste", "ocr"):
            raise ValueError(f"未知のサブコマンドです: {command}")

        try:
            request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"入力を UTF-8 の JSON として読み取れません: {error}") from error

        if sys.platform != "win32":
            raise RuntimeError("この機能は Windows でのみ利用できます")
        if not isinstance(request, dict):
            raise ValueError("リクエストには JSON オブジェクトを指定してください。")

        response = {"ok": True, "result": dispatch(command, request)}
    except (Exception, KeyboardInterrupt) as error:
        response = {"ok": False, "error": str(error) or type(error).__name__}

    output = json.dumps(response, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(output.encode("utf-8", errors="backslashreplace"))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
