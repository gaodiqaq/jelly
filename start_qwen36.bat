@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not defined AGENT_WEB_TOKEN (
    echo Please set AGENT_WEB_TOKEN before starting Jelly.
    exit /b 1
)
if not defined JELLY_QWEN_BASE_URL (
    echo Please set JELLY_QWEN_BASE_URL, for example http://127.0.0.1:8001/v1
    exit /b 1
)

echo Starting Jelly with the configured local model endpoint...
set "OPENAI_API_BASE=%JELLY_QWEN_BASE_URL%"
start "Jelly Web" /b ".venv\Scripts\agent.exe" web --host 127.0.0.1 --port 8000
echo Jelly is ready at http://127.0.0.1:8000
echo The access token remains in the process environment and is never printed.
