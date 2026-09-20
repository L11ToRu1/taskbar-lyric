@echo off
cd /d "%~dp0"
echo Building Lyric setting...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name LyricSetting --collect-all customtkinter setting.py
if errorlevel 1 exit /b 1
echo.
echo Done. Output: dist\LyricSetting.exe
pause
