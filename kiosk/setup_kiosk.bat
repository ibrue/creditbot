@echo off
REM One-time setup for the check-in kiosk on Windows 10/11.
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

echo Downloading face models...
.venv\Scripts\python download_models.py || goto :error

if not exist .env (
    copy .env.example .env
    echo.
    echo Opening .env — fill in KIOSK_API_URL (your NAS IP) and KIOSK_API_KEY.
    notepad .env
)

echo.
echo Setup complete! Double-click start_kiosk.bat to run the kiosk.
pause
exit /b 0

:error
echo.
echo Setup failed — see the error above.
pause
exit /b 1
