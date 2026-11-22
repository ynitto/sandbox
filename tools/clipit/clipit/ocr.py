"""Optional Windows OCR integration."""

import asyncio
from pathlib import Path


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
            "OCR を有効にするには、Windows で "
            "python -m pip install -r requirements-ocr.txt を実行し、ClipIt を再起動してください。"
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
