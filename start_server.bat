@echo off
REM Start the Discord bot + kiosk API together (run setup_server.bat once first).
REM Keeps a console window open so you can see the logs.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found — run setup_server.bat first.
    pause
    exit /b 1
)

.venv\Scripts\python start_all.py
pause
