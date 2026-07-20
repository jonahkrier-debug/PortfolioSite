@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-PortfolioSyncTask.ps1" %*
exit /b %errorlevel%
