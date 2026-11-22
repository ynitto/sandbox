@echo off
setlocal
cd /d "%~dp0" || exit /b 1
python -m pip install -r requirements.txt || exit /b 1
python -m clipit
exit /b %errorlevel%
