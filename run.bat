@echo off
REM ====================================================================
REM  Start the AI Recruitment System on this PC.
REM  Double-click. Then open your browser at:  http://127.0.0.1:8000/ui/
REM  Close this window (or press Ctrl+C) to stop it.
REM ====================================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo [X] Not set up yet. Double-click setup.bat first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
echo.
echo Starting... when you see "Application startup complete", open:
echo     http://127.0.0.1:8000/ui/
echo.
python -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
