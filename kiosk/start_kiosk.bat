@echo off
REM Start the check-in kiosk (run setup_kiosk.bat once first).
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found — run setup_kiosk.bat first.
    pause
    exit /b 1
)

REM pythonw = no console window behind the kiosk
start "" .venv\Scripts\pythonw.exe kiosk.py
