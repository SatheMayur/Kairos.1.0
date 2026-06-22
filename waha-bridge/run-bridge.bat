@echo off
REM ====================================================================
REM  Start the WhatsApp bridge (Windows). Keep this PC on + this running.
REM  First run shows a QR code - scan it from WhatsApp on your phone:
REM    WhatsApp -> Settings -> Linked Devices -> Link a Device
REM ====================================================================
cd /d "%~dp0"

REM Point the bridge at the live website + the shared secret (must match the
REM app's BRIDGE_API_SECRET). Change VERCEL_URL only if your site URL is different.
set VERCEL_URL=https://kgirdharlal-recruitment.vercel.app
set BRIDGE_API_KEY=kgirdharlal-bridge-secret

where node >nul 2>nul
if errorlevel 1 ( echo [X] Node.js not installed. Run setup-bridge.bat first. & pause & exit /b 1 )

echo Starting the WhatsApp bridge... (scan the QR code below on first run)
echo Connected to: %VERCEL_URL%
node bridge.js
pause
