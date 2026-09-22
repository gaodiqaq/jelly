@echo off
chcp 65001 >nul
powershell -NoProfile -Command ^
  "$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue;" ^
  "if (-not $listeners) { Write-Host 'No Jelly service found on port 8000.'; exit 0 };" ^
  "$stopped = $false;" ^
  "foreach ($listener in $listeners) {" ^
  "  $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess);" ^
  "  if ($process.CommandLine -match '(agent(?:\.exe)?|agent_shell)\s+web') {" ^
  "    Stop-Process -Id $listener.OwningProcess; Write-Host ('Stopped Jelly PID ' + $listener.OwningProcess); $stopped = $true" ^
  "  } else { Write-Warning ('Port 8000 belongs to another process; left PID ' + $listener.OwningProcess + ' untouched.') }" ^
  "};" ^
  "if (-not $stopped) { exit 2 }"
