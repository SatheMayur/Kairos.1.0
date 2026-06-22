@echo off
REM =====================================================================
REM  K. Girdharlal - START HERE  (one double-click runs everything)
REM   1) opens your recruitment dashboard in the browser
REM   2) starts WhatsApp (auto-installs a portable Node if needed)
REM  Nothing to install. Keep the WhatsApp window open; scan the QR once.
REM =====================================================================
cd /d "%~dp0"
echo Opening your dashboard...
start "" "https://kgirdharlal-recruitment.vercel.app/ui/"
echo Starting WhatsApp...
start "" cmd /k "%~dp0waha-bridge\START-WHATSAPP.bat"
echo.
echo Done. The dashboard is open in your browser, and a WhatsApp window is starting.
echo (On first run, scan the QR code shown in the WhatsApp window.)
timeout /t 6 >nul
