@echo off
REM ====================================================================
REM  Apna Sync - brings new Apna applicants into your HR system on its own.
REM  Just double-click this file. Keep the window open while it runs.
REM ====================================================================
cd /d "%~dp0"

REM --- 1. Is Node.js installed? -------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Node.js is not installed on this PC.
  echo   Please install it from https://nodejs.org  ^(click the big green button^),
  echo   then double-click this file again.
  echo.
  pause
  exit /b
)

REM --- 2. First run: install the bits this helper needs -------------------------
if not exist node_modules (
  echo.
  echo   First time setup. Installing what the helper needs.
  echo   This can take a few minutes. Please leave the window open and wait...
  echo.
  call npm install
  if errorlevel 1 (
    echo.
    echo   Setup could not finish - the install step failed.
    echo   Please check this PC has internet, then double-click this file again.
    echo.
    pause
    exit /b
  )
  call npx playwright install chromium
  if errorlevel 1 (
    echo.
    echo   Setup could not finish - the browser install step failed.
    echo   Please check this PC has internet, then double-click this file again.
    echo.
    pause
    exit /b
  )
)

REM --- 3. Make the settings file on first run -----------------------------------
if not exist config.json (
  if exist config.example.json (
    echo   Creating your settings file from the example...
    copy /Y config.example.json config.json >nul
  ) else (
    echo.
    echo   The example settings file is missing, so settings can't be created.
    echo   Please ask your developer to restore config.example.json in this folder.
    echo.
    pause
    exit /b
  )
)

REM --- 4. First time: sign in to Apna once --------------------------------------
if not exist session (
  echo.
  echo   First time: a browser will open so you can sign in to Apna once.
  echo   Type your Apna password ^(and OTP if asked^). When you are signed in,
  echo   click back on this black window and press ENTER.
  echo.
  call node sync.js --login
)

REM --- 5. Run, and restart on its own if it ever stops --------------------------
:run
echo.
echo   Checking Apna for new applicants now...
node sync.js
echo.
echo   Sync stopped. It will start again in 30 seconds.
echo   ^(To stop completely, just close this window.^)
timeout /t 30 /nobreak >nul
goto run
