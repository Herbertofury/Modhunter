@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>&1
if errorlevel 1 (
  echo Modhunter Desktop needs Node.js installed once. The standalone HTML and MO2 plugin do not.
  echo Get Node.js from https://nodejs.org/
  pause
  exit /b 1
)
if not exist node_modules\electron\dist\electron.exe (
  echo Installing the standalone Electron runtime...
  call npm install --no-audit --no-fund
  if errorlevel 1 (echo Install failed. Check the network then retry.&pause&exit /b 1)
)
call npm start
