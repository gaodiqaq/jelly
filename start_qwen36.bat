@echo off
chcp 65001 >nul
echo ============================================
echo    Jelly - Qwen3.6 本地模型
echo ============================================
echo.

cd /d D:\Agent-learning\jelly

echo [1/2] 启动 Jelly Web 服务...
start "Jelly Web" cmd /k "agent web --host 0.0.0.0 --port 8000"

echo.
echo [2/2] 等待服务启动...
timeout /t 5 /nobreak >nul

echo.
echo [3/3] 配置 OpenAI Provider API Base...
curl -s -X PUT http://localhost:8000/api/config ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer sk-ws-H.EDHLYXP.qOYo.MEQCIFQ_mvHFsqLDpLogr4m-Nd7Jrv2LwBvzcO-imtm8AiBx79ohUMPmphWQA_fzhELjcUPSJLRuvjUAaeUGSMyXPQ" ^
  -d {"provider":"openai","api_base":"http://192.168.19.238:8000/v1"}

echo.
echo ============================================
echo    配置完成！
echo    浏览器打开: http://localhost:8000
echo    令牌: sk-ws-H.EDHLYXP.qOYo.MEQCIFQ_mvHFsqLDpLogr4m-Nd7Jrv2LwBvzcO-imtm8AiBx79ohUMPmphWQA_fzhELjcUPSJLRuvjUAaeUGSMyXPQ
echo ============================================
echo.
pause
