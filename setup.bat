@echo off
echo === ScratchFrenzy Setup ===
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause
    exit /b 1
)

:: Create virtual environment
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

:: Activate and install deps
echo Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt --quiet

echo.
echo Setup complete!
echo.
echo To start ScratchFrenzy:
echo   1. Run start.bat
echo   2. Open http://localhost:8000 in your browser
echo.
pause
