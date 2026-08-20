@echo off
REM One-time setup to host the Discord bot + kiosk API on this Windows PC.
REM Requires Python 3.10+ from https://www.python.org/downloads/
REM (check "Add python.exe to PATH" in the installer).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" in the installer, then re-run this.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv .venv || goto :error

echo Installing dependencies...
.venv\Scripts\python -m pip install --upgrade pip || goto :error
.venv\Scripts\python -m pip install -r requirements.txt || goto :error

if not exist .env (
    copy .env.example .env
    echo.
    echo Opening .env — fill in DISCORD_TOKEN, channel IDs, and KIOSK_API_KEY.
    notepad .env
)

echo.
echo Setup complete! Double-click start_server.bat to run the bot + API.
pause
exit /b 0

:error
echo.
echo Setup failed — see the error above.
pause
exit /b 1
