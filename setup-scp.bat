@echo off
REM ==============================================================================
REM SCP AGENT OS — 1-COMMAND SETUP & LAUNCHER (WINDOWS)
REM Usage: .\setup-scp.bat [--server] [--no-start]
REM ==============================================================================
call "%~dp0launch.bat" setup %*
if errorlevel 1 exit /b %ERRORLEVEL%
if /i not "%~1"=="--no-start" if /i not "%~2"=="--no-start" (
    echo.
    echo [SCP] Starting services...
    call "%~dp0launch.bat" start
)
exit /b %ERRORLEVEL%
