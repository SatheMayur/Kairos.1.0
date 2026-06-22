@echo off
REM =====================================================================
REM  K. Girdharlal - START WHATSAPP  (one click, installs nothing for you)
REM  Double-click this. If Node.js isn't on the PC, it downloads a portable
REM  copy into this folder automatically (no admin, no system install),
REM  then starts the WhatsApp bridge. First run shows a QR code to scan.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title K. Girdharlal - WhatsApp Bridge

set "NODEVER=v20.18.1"
set "NODEDIR=node-%NODEVER%-win-x64"
set "LOCALNODE=%~dp0%NODEDIR%"

REM --- 1) Find Node: system PATH, or our local portable copy ---
where node >nul 2>nul && goto HAVE_NODE
if exist "%LOCALNODE%\node.exe" ( set "PATH=%LOCALNODE%;%PATH%" & goto HAVE_NODE )

echo Node.js was not found - downloading a portable copy (one time, ~30 MB)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; try { $u='https://nodejs.org/dist/%NODEVER%/%NODEDIR%.zip'; $z=Join-Path '%~dp0' 'node.zip'; Write-Host 'Downloading Node...'; Invoke-WebRequest -Uri $u -OutFile $z; Write-Host 'Unpacking...'; Expand-Archive -Path $z -DestinationPath '%~dp0' -Force; Remove-Item $z } catch { Write-Host $_.Exception.Message; exit 1 }"
if not exist "%LOCALNODE%\node.exe" (
  echo.
  echo [X] Could not set up Node automatically (no internet, or download blocked).
  echo     Please install Node.js 18+ from https://nodejs.org and double-click this again.
  pause
  exit /b 1
)
set "PATH=%LOCALNODE%;%PATH%"

:HAVE_NODE
for /f "delims=" %%v in ('node --version') do set "NODEV=%%v"
echo Using Node !NODEV!

REM --- 2) Install bridge packages once ---
if not exist "node_modules" (
  echo Installing the WhatsApp bridge (one time)...
  call npm install --no-audit --no-fund
)

REM --- 3) Point at the live site + shared secret, then run ---
set "VERCEL_URL=https://kgirdharlal-recruitment.vercel.app"
set "BRIDGE_API_KEY=kgirdharlal-bridge-secret"

echo.
echo ============================================================
echo   WhatsApp bridge starting - connected to %VERCEL_URL%
echo   If a QR code appears: on your phone open WhatsApp ->
echo   Settings -> Linked Devices -> Link a Device -> scan it.
echo   KEEP THIS WINDOW OPEN. Closing it turns WhatsApp off.
echo ============================================================
echo.
node bridge.js
echo.
echo (the bridge stopped) - press a key to close.
pause
