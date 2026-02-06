@echo off
REM Start the Subway Surfers kiosk on Windows
REM Add to Task Scheduler for autostart on boot

set KIOSK_ENABLED=true
set KIOSK_FULLSCREEN=true

cd /d "%~dp0"

REM Load .env if it exists (simple parser)
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "%%a=%%b"
    )
)

python bot.py
pause
