@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Sync-Portfolio.ps1" %*
exit /b %errorlevel%
