@echo off
title Social Credit Bot
echo ================================================
echo Social Credit Bot Launcher
echo ================================================
echo.

cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Download Python from https://python.org
    pause
    exit /b 1
)

REM Check for .env file
if not exist ".env" (
    echo ERROR: No .env file found!
    echo.
    echo Copy .env.example to .env and fill in your values.
    echo Then run this script again.
    pause
    exit /b 1
)

REM Install dependencies if needed
echo Checking dependencies...
pip install -r requirements.txt -q

echo.
echo Starting bot... Press Ctrl+C to stop.
echo ================================================
echo.

python start_bot.py

echo.
echo Bot stopped.
pause
