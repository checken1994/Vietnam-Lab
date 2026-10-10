@echo off
REM OPS-02: Thin shim delegating to unified launch.bat
call "%~dp0launch.bat" bootstrap %*
exit /b %ERRORLEVEL%
