@echo off
echo === ScratchFever — Manual Scrape ===
call .venv\Scripts\activate.bat
echo.
if "%1"=="" (
    echo Scraping ALL states...
    python scrape.py
) else (
    echo Scraping state: %*
    python scrape.py %*
)
echo.
pause
