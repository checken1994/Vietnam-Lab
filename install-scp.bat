@echo off
REM OPS-02: Thin shim creating data dirs at root (data\knowledge data\backups) and delegating to launch.bat
if not exist "%~dp0data\knowledge" mkdir "%~dp0data\knowledge"
if not exist "%~dp0data\backups" mkdir "%~dp0data\backups"
call "%~dp0launch.bat" install %*
exit /b %ERRORLEVEL%
