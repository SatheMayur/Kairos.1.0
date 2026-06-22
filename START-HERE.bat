@echo off
REM =====================================================================
REM   K. GIRDHARLAL — RECRUITMENT SYSTEM  ·  START EVERYTHING (one click)
REM   Double-click this single file. On the FIRST run it builds the virtual
REM   environment and installs everything; every run it starts the whole app
REM   + WhatsApp and opens your browser. Nothing else to do.
REM
REM   One-time requirement: Python 3.11+ (this file opens the download page
REM   for you if it's missing — tick "Add Python to PATH" during install).
REM   Node.js is downloaded automatically (portable) for WhatsApp.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title K. Girdharlal - Recruitment System

REM --- 1) Python present? (the only one-time install) ---
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo [X] Python is not installed. It is needed once.
  echo     Opening the download page... install Python 3.11+ and TICK
  echo     "Add Python to PATH", then double-click this file again.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

REM --- 2) First run: build the virtual environment + install ---
if not exist ".venv\Scripts\activate.bat" (
  echo [setup] First run - building the virtual environment and installing
  echo         packages. This takes a few minutes (only happens once)...
  python -m venv .venv || ( echo [X] Could not create .venv & pause & exit /b 1 )
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install -r requirements.txt || ( echo [X] Install failed - check internet, re-run. & pause & exit /b 1 )
) else (
  call ".venv\Scripts\activate.bat"
)
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [setup] Created .env. (Optional: paste your keys / the Neon DATABASE_URL
  echo         to use your live data - see MIGRATION.md Step 3.)
)

REM --- 3) Start the app (its own window). Relative paths = no quoting traps. ---
echo Starting the application...
start "Recruitment App" cmd /k ".venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"

REM --- 4) Start WhatsApp (its own window; auto-downloads Node if needed) ---
echo Starting WhatsApp...
start "WhatsApp Bridge" cmd /k "cd waha-bridge && call START-WHATSAPP.bat"

REM --- 5) Open the dashboard once the app is up ---
echo Waiting for the app to start, then opening your browser...
timeout /t 12 >nul
start "" "http://127.0.0.1:8000/ui/"

echo.
echo ============================================================
echo   Everything is running:
echo     - Dashboard : http://127.0.0.1:8000/ui/   (opened)
echo     - App window + WhatsApp window are open separately.
echo   First time: scan the QR in the WhatsApp window with your phone.
echo   Keep the windows open. Re-run this file anytime to start again.
echo ============================================================
timeout /t 8 >nul
