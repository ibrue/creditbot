@echo off
REM ===================================================================
REM  The only script you need.
REM
REM  Double-click this to run everything: the Discord bot, the kiosk
REM  API, and the check-in kiosk. It installs whatever is missing on
REM  the first run, so there is no separate setup step.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title CreditBot

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" in the installer, then run this again.
    pause
    exit /b 1
)

REM ---- Server settings (first run only) ----
if not exist .env (
    copy .env.example .env >nul
    echo.
    echo First run - opening the server settings in Notepad.
    echo Fill in DISCORD_TOKEN, your channel IDs, and KIOSK_API_KEY
    echo ^(any long random string^), then save and close Notepad.
    echo.
    notepad .env
)

REM ---- Kiosk settings (first run only) ----
REM Reuse the server's API key so there is nothing to copy by hand.
if not exist kiosk\.env (
    set "APIKEY="
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if /i "%%a"=="KIOSK_API_KEY" set "APIKEY=%%b"
    )
    if "!APIKEY!"=="" (
        echo.
        echo WARNING: KIOSK_API_KEY is empty in .env - the kiosk will not be
        echo able to reach the server until you set it. Opening .env again...
        notepad .env
        for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
            if /i "%%a"=="KIOSK_API_KEY" set "APIKEY=%%b"
        )
    )
    > kiosk\.env echo KIOSK_API_URL=http://localhost:8765
    >> kiosk\.env echo KIOSK_API_KEY=!APIKEY!
    echo Created kiosk\.env using the same API key as the server.
)

REM ---- Optional: local AI captions for check-in photos ----
set "OLLAMA_FOUND="
where ollama >nul 2>nul && set "OLLAMA_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_FOUND=1"
if not defined OLLAMA_FOUND (
    echo.
    echo Note: AI photo captions are off. Run setup_ollama.bat once to
    echo turn them on ^(optional, a few GB^). Photos still post without it.
)

echo.
echo Starting the server ^(Discord bot + kiosk API^)...
start "CreditBot Server" cmd /c start_server.bat

REM Give the API a moment so the kiosk's first request succeeds
timeout /t 8 /nobreak >nul

echo Starting the check-in kiosk...
start "CreditBot Kiosk" cmd /c kiosk\start_kiosk.bat

echo.
echo ===================================================================
echo  Everything is running, each in its own window.
echo.
echo  Both install what they need on first run, restart themselves if
echo  they crash, and update themselves from GitHub. Face recognition
echo  improves itself as members check in - no scripts to run.
echo.
echo  Close those two windows to stop.
echo ===================================================================
timeout /t 10 /nobreak >nul
