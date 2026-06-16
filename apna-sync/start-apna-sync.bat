@echo off
REM ====================================================================
REM  Apna Sync - pulls new applicants from Apna into the HR system.
REM  Keep this window open. It re-checks Apna on the schedule in config.json.
REM ====================================================================
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Node.js is not installed. Install it from https://nodejs.org  then run this again.
  echo.
  pause
  exit /b
)

if not exist node_modules (
  echo Installing required files the first time, please wait...
  call npm install
  call npx playwright install chromium
)

if not exist config.json (
  echo.
  echo   Setup needed: copy config.example.json to config.json and fill it in.
  echo.
  pause
  exit /b
)

if not exist session (
  echo.
  echo   First time: a browser will open so you can sign in to Apna once.
  echo.
  call node sync.js --login
)

:run
node sync.js
echo.
echo   Sync stopped. Restarting in 30 seconds... (close this window to stop)
timeout /t 30 /nobreak >nul
goto run
