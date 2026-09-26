@echo off
cd /d "%~dp0"
echo Building Lyric...
taskkill /f /im Lyric.exe >nul 2>&1
taskkill /f /im TaskBarLyric.exe >nul 2>&1
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
rem version number lives in ..VERSION; this writes version_info.txt next to us
python "..\make_version.py"
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Lyric --version-file version_info.txt --hidden-import winrt.windows.media.control --hidden-import winrt.windows.foundation --hidden-import winrt.windows.foundation.collections lyric.py
if errorlevel 1 exit /b 1
rem keep the root copy in sync, or double-clicking it runs stale code
copy /y "dist\Lyric.exe" "..\TaskBarLyric.exe" >nul
echo.
echo Done. Output: dist\Lyric.exe  (+ ..\TaskBarLyric.exe)
pause
