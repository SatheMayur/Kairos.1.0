@echo off
REM ====================================================================
REM  WhatsApp bridge - one-time setup (Windows).
REM  This connects your WhatsApp to the recruitment system.
REM  Requires Node.js 18+ (https://nodejs.org). Double-click to set up.
REM ====================================================================
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js is not installed.
  echo     Install Node.js 18 or newer from https://nodejs.org  then re-run this file.
  pause
  exit /b 1
)

echo Installing bridge packages ...
call npm install
if errorlevel 1 ( echo [X] npm install failed. Check internet and re-run. & pause & exit /b 1 )

echo Installing pm2 (keeps the bridge running 24/7) ...
call npm install -g pm2

echo.
echo === Bridge setup complete! ===
echo Next: double-click run-bridge.bat, then scan the QR code with WhatsApp
echo (WhatsApp phone - Settings - Linked Devices - Link a Device).
pause
