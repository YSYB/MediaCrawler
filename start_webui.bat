@echo off
chcp 65001 >nul
echo ========================================
echo   MediaCrawler WebUI Launcher
echo ========================================
echo.

set USERNAME=%USERNAME%
set PATH=C:\nvm4w\nodejs;%PATH%
set EXECJS_RUNTIME=Node

cd /d D:\MediaCrawler

:: Start WebUI service
echo [1/2] Starting WebUI (http://localhost:8080)...
start "MediaCrawler WebUI" .venv\Scripts\python.exe -m uvicorn api.main:app --port 8080 --host 127.0.0.1

:: Wait for service
echo [2/2] Opening browser...
timeout /t 3 /nobreak >nul

:: Open browser
start "" "http://localhost:8080"

echo.
echo ========================================
echo   WebUI Started!
echo   Press any key to close this window
echo ========================================
pause >nul
