@echo off
chcp 65001 >nul
rem ============================================
rem  Start jelly web service on port 8000
rem  Run in foreground; press Ctrl+C to stop.
rem ============================================
cd /d "%~dp0"
echo Starting jelly web service at http://localhost:8000 ...
".venv\Scripts\agent.exe" web --host 0.0.0.0 --port 8000
echo.
echo Service stopped.
pause