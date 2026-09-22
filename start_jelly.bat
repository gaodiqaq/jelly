@echo off
chcp 65001 >nul
rem ============================================
rem  Start jelly web service on port 8000
rem  Run in foreground; press Ctrl+C to stop.
rem ============================================
cd /d "%~dp0"
echo Starting jelly web service at http://127.0.0.1:8000 ...
".venv\Scripts\agent.exe" web --host 127.0.0.1 --port 8000
echo.
echo Service stopped.
pause
