@echo off
REM Start the Discord bot + kiosk API together.
REM Installs everything it needs on first run (Python must be installed
REM with "Add python.exe to PATH" checked) and auto-restarts on crashes.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Install it from https://www.python.org/downloads/
        echo and check "Add python.exe to PATH" in the installer, then re-run this.
        pause
        exit /b 1
    )
    echo First run - creating virtual environment...
    python -m venv .venv || goto :error
)

REM Make sure dependencies are installed (fast no-op when already there)
.venv\Scripts\python -c "import discord, fastapi, uvicorn, requests, dotenv, apscheduler" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    .venv\Scripts\python -m pip install --upgrade pip || goto :error
    .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)

if not exist .env (
    copy .env.example .env
    echo.
    echo Opening .env — fill in DISCORD_TOKEN, channel IDs, and KIOSK_API_KEY,
    echo then save, close Notepad, and the server will start.
    notepad .env
)

:run
.venv\Scripts\python start_all.py
echo.
echo Server stopped (exit code %errorlevel%). Restarting in 5 seconds...
echo Close this window to stop for real.
timeout /t 5 /nobreak >nul
goto :run

:error
echo.
echo Setup failed — see the error above.
pause
exit /b 1
