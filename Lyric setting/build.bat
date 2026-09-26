@echo off
cd /d "%~dp0"
echo Building Lyric setting...
taskkill /f /im LyricSetting.exe >nul 2>&1
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
rem version number lives in ..VERSION; this writes version_info.txt next to us
python "..\make_version.py"
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name LyricSetting --version-file version_info.txt --collect-all customtkinter setting.py
if errorlevel 1 exit /b 1
echo.
echo Done. Output: dist\LyricSetting.exe
pause
