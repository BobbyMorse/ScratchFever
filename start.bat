@echo off
title ScratchFrenzy Server
echo === ScratchFrenzy ===
echo.

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo Cleared port 8000.

call .venv\Scripts\activate.bat
echo.
echo Server running at http://localhost:8000
echo Press Ctrl+C to stop.
echo.
start "" http://localhost:8000
python run.py
echo.
echo Server stopped. Press any key to close.
pause >nul
