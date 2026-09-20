@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title SCP Start

cd /d "%~dp0"

echo.
echo ============================================================
echo   SCP START - Khoi dong toan bo he thong (API-Only)
echo ============================================================
echo.

REM --- Check .env ---
if not exist ".env" (
    echo [FAIL] KHONG TIM THAY .env
    echo    Chay install-scp.bat truoc, hoac copy .env.example thanh .env
    echo    Dien OPENROUTER_API_KEY trong .env
    echo.
    pause
    exit /b 1
)

REM --- Check Python (system, not venv) ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Python khong tim thay trong PATH.
    echo    Cai dat Python 3.10+ va dam bao trong PATH.
    echo.
    pause
    exit /b 1
)

REM --- Check dashboard dependencies ---
if not exist "dashboard\node_modules\next\dist\bin\next" (
    echo [INFO] Dashboard dependencies chua co - dang cai theo bun.lock...
    pushd dashboard
    bun install --frozen-lockfile
    if errorlevel 1 (
        echo [FAIL] Khong cai duoc dashboard dependencies.
        popd
        pause
        exit /b 1
    )
    popd
)

REM --- Build dashboard from current source before any restart ---
pushd dashboard
bun run build
if errorlevel 1 (
    echo [FAIL] Dashboard build that bai - giu nguyen cac service dang chay.
    popd
    pause
    exit /b 1
)
popd

REM --- Ensure data dir exists (Windows fix) ---
if not exist "data" mkdir data

REM --- Stop old instances (kill by window-title + port, NOT by image name) ---
echo [0/3] Dung services cu (neu co)...
taskkill /f /fi "WINDOWTITLE eq SCP-Loop-Scheduler*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SCP-Python*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SCP-Dashboard*" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3030 " ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 " ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1
timeout /t 3 /nobreak >nul

echo.
echo [INFO] Khoi dong 3 child services (API-Only, khong Ollama)...
echo.

REM --- 1. Loop Scheduler (port 3030) ---
echo [1/3] Loop Scheduler - port 3030
start "SCP-Loop-Scheduler" cmd /k "cd /d %~dp0mini-services\loop-scheduler && set SCP_BASE_URL=http://127.0.0.1:8000 && set SCP_INTERNAL_URL=http://127.0.0.1:8000 && set LOOP_SCHEDULER_URL=http://127.0.0.1:3030 && set LLM_BRIDGE_URL=http://127.0.0.1:8081 && set LOOP_LOG_PATH=%~dp0data\loop_runs.jsonl && bun run dev"
timeout /t 1 /nobreak >nul

REM --- 2. SCP Python (port 8000) — API-Only, no Ollama, no venv ---
echo [2/3] SCP Python - port 8000 (boot ~15s, vui long doi...)
start "SCP-Python" cmd /k "cd /d %~dp0 && python -m scp 8000"

REM --- 3. Dashboard Next.js (port 3000) ---
echo [3/3] Dashboard Next.js - port 3000 (standalone build)
start "SCP-Dashboard" cmd /k "cd /d %~dp0dashboard && set SCP_INTERNAL_URL=http://127.0.0.1:8000 && set LOOP_SCHEDULER_URL=http://127.0.0.1:3030 && bun run start"

REM --- Wait for SCP boot ---
echo.
echo [INFO] Doi SCP khoi dong (polling /health, toi da 180s)...
set /a COUNT=0
:waitloop
set /a COUNT+=1
curl -sf --max-time 2 http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 (
    echo [OK] SCP san sang sau ~!COUNT! giay
    goto :ready
)
if !COUNT! geq 90 (
    echo [WARN] SCP chua san sang sau 180s - kiem tra cua so SCP-Python
    goto :ready
)
timeout /t 2 /nobreak >nul
goto :waitloop

:ready
echo.
echo ============================================================
echo   [DONE] SCP SYSTEM DANG CHAY! (API-Only — khong Ollama)
echo ============================================================
echo.
echo   Dashboard:       http://localhost:3000
echo   SCP /health:     http://127.0.0.1:8000/health
echo   Loop Scheduler:  http://127.0.0.1:3030/
echo.
echo   3 cua so child dang chay:
echo     - SCP-Loop-Scheduler
echo     - SCP-Python
echo     - SCP-Dashboard
echo.
echo   Dung tat ca: chay stop-scp.bat
echo.

REM --- Try to open browser ---
start http://localhost:3000

echo.
echo Nhan phim bat ky de thoat script nay (services van chay)
pause >nul
