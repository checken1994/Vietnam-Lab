@echo off
REM Self-elevating: xoa .pytest_cache bi ACL khoa (chan docker build tu D:\scp)
REM诞生 2026-10-25 — sau khi chay xong, build docker compose truc tiep tu D:\scp hoat dong binh thuong
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Can quyen admin - dang xin thang qua UAC...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
takeown /f "D:\scp\.pytest_cache" /r /d y >nul 2>&1
icacls "D:\scp\.pytest_cache" /grant "%USERNAME%":F /t >nul 2>&1
rmdir /s /q "D:\scp\.pytest_cache"
if exist "D:\scp\.pytest_cache" (echo THAT BAI - van con) else (echo DA XOA .pytest_cache - build tu D:\scp gio hoat dong)
pause
