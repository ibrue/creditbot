@echo off
REM Install Ollama + a local vision model for check-in photo captions.
REM Safe to re-run; skips anything already installed. The model download
REM is a few GB, so give it a few minutes on the first run.
cd /d "%~dp0"
setlocal enabledelayedexpansion

where ollama >nul 2>nul
if not errorlevel 1 goto :have_ollama

REM Ollama installs to %LOCALAPPDATA%\Programs\Ollama — maybe it's there
REM but not on PATH yet (fresh install, old console)
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\Ollama;!PATH!"
    goto :have_ollama
)

echo Installing Ollama...
winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo winget unavailable - downloading the installer directly...
    powershell -NoProfile -Command "Invoke-WebRequest -Uri https://ollama.com/download/OllamaSetup.exe -OutFile $env:TEMP\OllamaSetup.exe" || goto :error
    "%TEMP%\OllamaSetup.exe" /VERYSILENT /SUPPRESSMSGBOXES || goto :error
)
set "PATH=%LOCALAPPDATA%\Programs\Ollama;!PATH!"
where ollama >nul 2>nul || goto :error

:have_ollama
echo Ollama is installed.

REM Pick a vision model that fits this PC: llava needs ~8GB free RAM,
REM moondream is much smaller and fine for fun captions
set MODEL=llava
for /f %%i in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"') do set RAM=%%i
if defined RAM if !RAM! LSS 12 set MODEL=moondream
echo This PC has !RAM! GB RAM - using the "!MODEL!" vision model.

echo Downloading the model (one-time, a few GB)...
ollama pull !MODEL! || goto :error

REM Point the server at the chosen model in .env
if exist .env (
    findstr /b "OLLAMA_MODEL=" .env >nul 2>nul
    if not errorlevel 1 (
        powershell -NoProfile -Command "(Get-Content .env) -replace '^OLLAMA_MODEL=.*', 'OLLAMA_MODEL=!MODEL!' | Set-Content .env"
    ) else (
        echo OLLAMA_MODEL=!MODEL!>> .env
    )
    echo Set OLLAMA_MODEL=!MODEL! in .env
)

echo.
echo Done! Restart the server (close and reopen start_server.bat) and
echo kiosk check-in photos will get AI captions.
pause
exit /b 0

:error
echo.
echo Ollama setup failed - see the error above. You can also install it
echo manually from https://ollama.com/download and run: ollama pull llava
pause
exit /b 1
