@echo off
title K. Girdharlal WhatsApp Bridge
color 0A
echo.
echo  =============================================
echo   K. Girdharlal International
echo   WhatsApp Bridge — Starting...
echo  =============================================
echo.
cd /d "%~dp0"
echo  Checking Node.js...
node --version
echo.
echo  Starting WhatsApp bridge...
echo  Once connected, this window must stay open.
echo  To stop: close this window or press Ctrl+C
echo.
node bridge.js
echo.
echo  Bridge stopped. Close this window or restart.
pause
