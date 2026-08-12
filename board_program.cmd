@echo off
rem Extra args pass through, e.g.: board_program.cmd -NoNicSetup -NoPing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0program_board.ps1" -MaxEthRetries 0 %*
exit /b %ERRORLEVEL%