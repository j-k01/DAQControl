@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\set_board_ethernet.ps1" -InterfaceAlias "Ethernet" -SkipTest
exit /b %ERRORLEVEL%
