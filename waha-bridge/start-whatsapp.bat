@echo off
title K. Girdharlal WhatsApp Bridge
color 0A
echo.
echo  =============================================
echo   K. Girdharlal International
echo   WhatsApp Bridge - Starting...
echo  =============================================
echo.
cd /d "%~dp0"

echo  Checking Node.js...
where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [!] Node.js is NOT installed on this computer.
  echo      WhatsApp cannot start without it.
  echo.
  echo      HOW TO FIX:
  echo        1. Open this website:  https://nodejs.org
  echo        2. Download the big green "LTS" button
  echo        3. Install it - just keep clicking Next
  echo        4. Then double-click this file again
  echo.
  pause
  exit /b
)
node --version

if not exist "node_modules" (
  echo.
  echo  First-time setup: downloading the parts WhatsApp needs.
  echo  This happens only once and may take a minute. Please wait...
  echo.
  call npm install
  echo.
)

echo.
echo  Starting WhatsApp bridge...
echo  Once connected, KEEP THIS WINDOW OPEN.
echo  To stop: close this window or press Ctrl+C
echo.
node bridge.js
echo.
echo  Bridge stopped. Close this window or restart.
pause
