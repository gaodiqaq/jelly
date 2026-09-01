@echo off
chcp 65001 >nul
rem ============================================
rem  Stop jelly web service (kills whatever listens on :8000)
rem ============================================
setlocal enabledelayedexpansion
set FOUND=
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    set FOUND=1
    echo Killing PID %%P ...
    taskkill /F /PID %%P >nul 2>&1
)
if not defined FOUND (
    echo No jelly service found listening on port 8000.
) else (
    echo Jelly service stopped.
)
pause