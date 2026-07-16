@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0program_board.ps1" -Vivado 2024.1 -MaxEthRetries 0
exit /b %ERRORLEVEL%
