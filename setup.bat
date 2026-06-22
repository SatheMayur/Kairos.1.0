@echo off
REM ====================================================================
REM  AI Recruitment System - one-time setup for a new Windows PC
REM  Double-click this file. It creates the virtual environment and
REM  installs everything. Run it once.
REM ====================================================================
cd /d "%~dp0"
echo.
echo === AI Recruitment System: setup starting ===
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed.
  echo     Install Python 3.11 or newer from https://www.python.org/downloads/
  echo     IMPORTANT: tick "Add Python to PATH" during install, then re-run this file.
  pause
  exit /b 1
)

echo [1/4] Creating the virtual environment (.venv) ...
python -m venv .venv
if errorlevel 1 ( echo [X] Could not create the virtual environment. & pause & exit /b 1 )

echo [2/4] Activating it ...
call ".venv\Scripts\activate.bat"

echo [3/4] Installing required packages (this can take a few minutes) ...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 ( echo [X] Package install failed. Check your internet connection and re-run. & pause & exit /b 1 )

echo [4/4] Preparing your settings file ...
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo     Created .env from the template.
) else (
  echo     .env already exists - left as-is.
)

echo.
echo === Setup complete! ===
echo.
echo NEXT:
echo   1) Open the file ".env" in Notepad and fill in your keys
echo      (see MIGRATION.md - "Step 3" for exactly which ones and where to get them).
echo   2) Double-click "run.bat" to start the system.
echo.
pause
