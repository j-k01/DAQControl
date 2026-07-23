@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover_ethernet.ps1" %*
exit /b %ERRORLEVEL%
