@echo off
setlocal
cd /d "%~dp0desktop"

REM --- Check electron binary ---
if not exist "node_modules\electron\dist\electron.exe" (
  echo [SCP] Chua cai desktop dependencies. Dang chay npm install...
  call npm install
  if errorlevel 1 (
    echo [FAIL] npm install that bai.
    pause
    exit /b 1
  )
)

echo [SCP] Dang khoi dong SCP DNA Desktop - model 1.6...
REM Clean stale lock if no responsive electron window exists
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (!(Get-Process -Name electron -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })) { Stop-Process -Name electron -Force -ErrorAction SilentlyContinue }"

REM Launch Electron cleanly in detached mode without leaving a CMD window
start "" "node_modules\electron\dist\electron.exe" .
exit /b 0
