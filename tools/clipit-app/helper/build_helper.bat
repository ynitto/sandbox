@echo off
setlocal
cd /d "%~dp0" || exit /b 1
python -m pip install pyinstaller || exit /b 1
set "ocr_options="
python -c "from winrt.windows.media.ocr import OcrEngine" >nul 2>&1
if not errorlevel 1 set "ocr_options=--collect-all winrt"
python -m PyInstaller --noconfirm --clean --onefile --console --name clipit-helper --paths . %ocr_options% clipit_helper.py
exit /b %errorlevel%
