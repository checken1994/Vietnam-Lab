@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title SCP One-Command Bootstrap

REM ============================================================
REM  SCP ONE-COMMAND BOOTSTRAP (Windows 10/11)
REM  Muc tieu: tu may tinh khong co gi -> SCP chay duoc.
REM  Cach dung:  curl -O <raw-url>/bootstrap-scp.bat  roi chay file nay.
REM  Nguon mac dinh: https://github.com/checken1994/Vietnam-Lab.git
REM  (c) SCP project - bootstrap này chỉ cài công cụ public, KHÔNG
REM  tự lấy secret nào từ internet; OPENROUTER_API_KEY do user nhập.
REM ============================================================

set "REPO_URL=https://github.com/checken1994/Vietnam-Lab.git"
set "TARGET_DIR=%USERPROFILE%\scp"
if not "%~1"=="" set "TARGET_DIR=%~1"
set "LOG=%TARGET_DIR%\bootstrap.log"

echo.
echo ============================================================
echo   SCP BOOTSTRAP - mot lenh tu PC trong sang he thong chay
echo   Repo : %REPO_URL%
echo   Dich : %TARGET_DIR%
echo ============================================================
echo.

REM --- [0/6] Quyền administrator KHÔNG bắt buộc; winget tự nâng khi cần.
where winget >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Khong tim thay winget. Windows 10 1809+ / Windows 11 deu co san.
    echo        Cai "App Installer" tu Microsoft Store roi chay lai.
    pause
    exit /b 1
)

REM --- [1/6] Lay hoac cap nhat source ---
echo [1/6] Tai source SCP...
if exist "%TARGET_DIR%\.git" (
    echo   Repo da ton tai - git pull...
    git -C "%TARGET_DIR%" pull --ff-only
    if errorlevel 1 (
        echo   [FAIL] git pull that bai - kiem tra mang hoac conflict.
        pause
        exit /b 1
    )
) else (
    where git >nul 2>&1
    if errorlevel 1 (
        echo   Git chua co - cai qua winget...
        winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
        if errorlevel 1 (
            echo   [FAIL] Khong cai duoc Git.
            pause
            exit /b 1
        )
        set "PATH=%PATH%;%ProgramFiles%\Git\cmd"
    )
    echo   Git clone %REPO_URL%...
    git clone --depth 1 %REPO_URL% "%TARGET_DIR%"
    if errorlevel 1 (
        echo   [FAIL] git clone that bai.
        pause
        exit /b 1
    )
)
echo   [OK] Source san sang tai %TARGET_DIR%

REM --- [2/6] Python 3.12+ ---
echo [2/6] Kiem tra / cai Python 3.12...
set "PYSATISFIED=0"
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYSATISFIED=1"
if "%PYSATISFIED%"=="0" (
    python --version 2>nul | findstr /r "3\.1[2-9]" >nul
    if not errorlevel 1 set "PYSATISFIED=1"
)
if "%PYSATISFIED%"=="1" (
    echo   [OK] Python 3.12+ san sang.
) else (
    echo   Cai Python 3.12 qua winget...
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo   [FAIL] Khong cai duoc Python 3.12.
        pause
        exit /b 1
    )
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
    py -3.12 --version >nul 2>&1
    if errorlevel 1 (
        echo   [FAIL] Python 3.12 van khong nhan dien sau khi cai. Mo CMD moi roi chay lai.
        pause
        exit /b 1
    )
    echo   [OK] Python 3.12 da cai.
)

REM --- [3/6] Node.js LTS (dung de cai bun) ---
echo [3/6] Kiem tra / cai Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo   [FAIL] Khong cai duoc Node.js.
        pause
        exit /b 1
    )
    set "PATH=%PATH%;%ProgramFiles%\nodejs"
)
echo   [OK] Node san sang.

REM --- [4/6] Bun ---
echo [4/6] Kiem tra / cai Bun...
where bun >nul 2>&1
if errorlevel 1 (
    call npm install -g bun
    if errorlevel 1 (
        echo   [FAIL] Khong cai duoc Bun.
        pause
        exit /b 1
    )
)
echo   [OK] Bun san sang.

REM --- [5/6] Chay installer chinh cua repo (deps + .env + secrets + verify) ---
echo [5/6] Chay install-scp.bat (deps + secure .env + boot verify)...
pushd "%TARGET_DIR%"
call install-scp.bat
if errorlevel 1 (
    popd
    echo   [FAIL] install-scp.bat that bai - xem log phia tren.
    pause
    exit /b 1
)
popd

REM --- [6/6] Khoi dong ngam toan bo he thong ---
echo [6/6] Khoi dong SCP (silent, 4 service)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%TARGET_DIR%\scripts\ops\start_scp_silent.ps1" -OpenBrowser true
if errorlevel 1 (
    echo   [FAIL] start_scp_silent.ps1 that bai - xem %TARGET_DIR%\data\service-logs\
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   [DONE] SCP DA CHAY!
echo   Dashboard : http://localhost:3000
echo   API       : http://127.0.0.1:8000/health
echo   Log       : %TARGET_DIR%\data\service-logs\
echo   Luu y     : dien OPENROUTER_API_KEY vao %TARGET_DIR%\.env
echo               roi restart neu muon LLM hoi that.
echo ============================================================
echo.
pause
