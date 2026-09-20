@echo off
chcp 65001 >nul
title SCP Stop

echo.
echo ============================================================
echo   SCP STOP - Dung tat ca services
echo ============================================================
echo.

echo [1/2] Dong cac cua so SCP...
REM LLM-Gateway on port #LLM-Gateway-removed is an external dependency and is never stopped by SCP.
taskkill /f /fi "WINDOWTITLE eq SCP-Loop-Scheduler*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SCP-Python*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SCP-Dashboard*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SCP-Desktop-App*" >nul 2>&1
taskkill /f /im electron.exe >nul 2>&1

echo [2/2] Kill processes dang giu ports...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3030 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a (port 3030)
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a (port 8000)
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a (port 3000)
    taskkill /f /pid %%a >nul 2>&1
)

timeout /t 2 /nobreak >nul

echo.
echo ============================================================
echo   [DONE] Tat ca services da dung
echo ============================================================
echo.

echo Kiem tra ports (phai trong):
netstat -aon | findstr ":3030 :8000 :3000 " | findstr "LISTENING"
if errorlevel 1 echo   (khong co process nao con chay)

echo.
echo Nhan phim bat ky de thoat...
pause >nul
