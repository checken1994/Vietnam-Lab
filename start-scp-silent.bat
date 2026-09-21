@echo off
setlocal
chcp 65001 >nul
title SCP Silent Start

REM Khởi chạy ngầm toàn bộ dịch vụ SCP không để lại cửa sổ CMD
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ops\start_scp_silent.ps1" -OpenBrowser

exit /b 0
