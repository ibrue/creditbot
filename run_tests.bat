@echo off
REM Run the test suite. Installs what it needs on first run (Python must be
REM installed with "Add python.exe to PATH" checked).
REM
REM No .env, bot token, or Ollama model is needed: the tests use a
REM throwaway database and stub out Discord and Ollama. Your real
REM social_credit.db is never touched.
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

REM Make sure test dependencies are installed (fast no-op when already there)
.venv\Scripts\python -c "import pytest, httpx" >nul 2>nul
if errorlevel 1 (
    echo Installing test dependencies...
    .venv\Scripts\python -m pip install --upgrade pip || goto :error
    .venv\Scripts\python -m pip install -r requirements-dev.txt || goto :error
)

echo.
.venv\Scripts\python -m pytest
set RESULT=%errorlevel%

echo.
if %RESULT%==0 (
    echo All tests passed.
) else (
    echo Some tests FAILED - see the output above.
)
pause
exit /b %RESULT%

:error
echo.
echo Setup failed - see the error above.
pause
exit /b 1
