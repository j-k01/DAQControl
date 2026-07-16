@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\unset_board_ethernet.ps1" -InterfaceAlias "Ethernet"
exit /b %ERRORLEVEL%
