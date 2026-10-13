@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title SCP Unified Launcher

REM ==============================================================================
REM OPS-02: Unified SCP Entrypoint Launcher (launch.bat)
REM Consolidates 7 divergent .bat launchers into a single authoritative entrypoint.
REM Usage:
REM   launch.bat [command] [options]
REM Commands:
REM   start      Start all SCP services (options: --windowed, --silent, --browser)
REM   stop       Stop all running SCP services and free listening ports
REM   desktop    Launch SCP Desktop application (Electron)
REM   browser    Launch dedicated browser session with CDP remote debugging
REM   install    Fresh-machine dependency installation and environment check
REM   bootstrap  Bootstrap development venv and dependencies
REM   test       Run portable reality test suite or pytests
REM   audit      Run T00 meta-audit, secret scanner, and dependency checks
REM   help       Display this usage message
REM ==============================================================================

cd /d "%~dp0"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=start"

if /i "%CMD%"=="help" goto CMD_HELP
if /i "%CMD%"=="--help" goto CMD_HELP
if /i "%CMD%"=="-h" goto CMD_HELP
if /i "%CMD%"=="start" goto CMD_START
if /i "%CMD%"=="up" goto CMD_START
if /i "%CMD%"=="stop" goto CMD_STOP
if /i "%CMD%"=="down" goto CMD_STOP
if /i "%CMD%"=="desktop" goto CMD_DESKTOP
if /i "%CMD%"=="browser" goto CMD_BROWSER
if /i "%CMD%"=="install" goto CMD_INSTALL
if /i "%CMD%"=="bootstrap" goto CMD_BOOTSTRAP
if /i "%CMD%"=="test" goto CMD_TEST
if /i "%CMD%"=="audit" goto CMD_AUDIT

echo [OPS-02 FAIL] Unknown command '%CMD%'. Run 'launch.bat help' for usage.
exit /b 1

:CMD_HELP
echo ==============================================================================
echo   SCP UNIFIED LAUNCHER (OPS-02)
echo ==============================================================================
echo Usage: launch.bat [command] [options]
echo.
echo Available commands:
echo   start [--windowed^|--silent^|--browser]  Start SCP backend and dashboard services
echo   stop                                   Stop all background SCP services
echo   desktop                                Launch SCP Electron Desktop application
echo   browser                                Launch remote-debug browser session
echo   install                                Install dependencies and check environment
echo   bootstrap                              Bootstrap Python venv and dev dependencies
echo   test [args]                            Run verification test suite
echo   audit                                  Run T00 meta-audit and secret scanners
echo   help                                   Show this help message
echo ==============================================================================
exit /b 0

:CMD_START
shift
set "OPT=%~1"
if /i "%OPT%"=="--windowed" (
    if not exist "%~dp0dashboard\node_modules\next\dist\bin\next" (
        echo [SCP] Installing dashboard dependencies...
        pushd "%~dp0dashboard"
        call bun install --frozen-lockfile
        popd
    )
    if not exist "%~dp0dashboard\.next\BUILD_ID" (
        echo [SCP] Building dashboard production bundle...
        pushd "%~dp0dashboard"
        call bun run build
        popd
    )
    start "SCP-LLM-Bridge" cmd /k "cd /d %~dp0 && set SCP_LLM_BRIDGE_PORT=8081 && python scripts\llm_bridge_server.py"
    start "SCP-Loop-Scheduler" cmd /k "cd /d %~dp0 && set LOOP_SCHEDULER_URL=http://127.0.0.1:3030 && python -m scp.LoopScheduler"
    start "SCP-Python" cmd /k "cd /d %~dp0 && set SCP_INTERNAL_URL=http://127.0.0.1:8000 && python -m scp 8000"
    start "SCP-Dashboard" cmd /k "cd /d %~dp0dashboard && set SCP_INTERNAL_URL=http://127.0.0.1:8000 && set LOOP_SCHEDULER_URL=http://127.0.0.1:3030 && bun run start"
    exit /b %ERRORLEVEL%
)
if /i "%OPT%"=="--silent" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ops\start_scp_silent.ps1"
    exit /b %ERRORLEVEL%
)
if /i "%OPT%"=="--browser" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ops\start_scp_silent.ps1" -OpenBrowser true
    exit /b %ERRORLEVEL%
)
REM Default start: silent background launcher with browser
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ops\start_scp_silent.ps1" -OpenBrowser true
exit /b %ERRORLEVEL%

:CMD_STOP
echo [SCP] Stopping all running services...
taskkill /f /t /fi "WINDOWTITLE eq SCP-LLM-Bridge*" >nul 2>&1
taskkill /f /t /fi "WINDOWTITLE eq SCP-Loop-Scheduler*" >nul 2>&1
taskkill /f /t /fi "WINDOWTITLE eq SCP-Python*" >nul 2>&1
taskkill /f /t /fi "WINDOWTITLE eq SCP-Dashboard*" >nul 2>&1
taskkill /f /t /fi "WINDOWTITLE eq SCP-Desktop-App*" >nul 2>&1
taskkill /f /t /im electron.exe >nul 2>&1

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8081 " ^| findstr "LISTENING"') do (
    taskkill /f /t /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3030 " ^| findstr "LISTENING"') do (
    taskkill /f /t /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /f /t /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /f /t /pid %%a >nul 2>&1
)
echo [SCP] All services stopped.
exit /b 0

:CMD_DESKTOP
cd /d "%~dp0desktop"
if not exist "node_modules\electron\dist\electron.exe" (
    echo [SCP] Installing desktop dependencies...
    call npm install
)
echo [SCP] Launching SCP Desktop App...
start "" "node_modules\electron\dist\electron.exe" .
exit /b 0

:CMD_BROWSER
shift
call "%~dp0scripts\ops\start_browser_session.bat" %* 2>nul || (
    if exist "%~dp0start-scp-browser.bat" (
        call "%~dp0start-scp-browser.bat" %*
    )
)
exit /b %ERRORLEVEL%

:CMD_INSTALL
shift
echo [SCP] Running environment install...
python -m pip install -r "%~dp0scp\requirements.txt"
exit /b %ERRORLEVEL%

:CMD_BOOTSTRAP
shift
echo [SCP] Bootstrapping environment...
python -m pip install -r "%~dp0scp\requirements-dev.txt"
exit /b %ERRORLEVEL%

:CMD_TEST
shift
python scripts\run_reality_tests_portable.py %*
exit /b %ERRORLEVEL%

:CMD_AUDIT
echo [SCP] Running audits...
python tools\t00_meta_audit.py && python tools\scan_secrets.py && python tools\check_coverage_thresholds.py --verify-config
exit /b %ERRORLEVEL%
