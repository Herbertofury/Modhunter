@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Check_MO2_Readiness.ps1" -SaveReport
set "result=%ERRORLEVEL%"
echo.
pause
exit /b %result%
