@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_MO2_Plugin.ps1" %*
if errorlevel 1 (
  echo.
  echo Modhunter plugin installation did not complete. Read the message above.
  pause
  exit /b 1
)
pause
