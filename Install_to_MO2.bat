@echo off
setlocal EnableExtensions
set "SOURCE=%~dp0mo2_update_arsenal"
if not exist "%SOURCE%\__init__.py" (
 echo [ERROR] Missing mo2_update_arsenal plugin folder next to installer.
 pause
 exit /b 1
)
if not exist "%SOURCE%\web\index.html" (
 echo [ERROR] Missing bundled dashboard HTML.
 pause
 exit /b 1
)
set "MO2_ROOT=%~1"
if not defined MO2_ROOT (
 echo MO2 UPDATE ARSENAL - Installer
 echo Select your Mod Organizer 2 installation directory.
 for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description='Select MO2 folder containing ModOrganizer.exe'; if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}"`) do set "MO2_ROOT=%%P"
)
if not defined MO2_ROOT (
 set /p "MO2_ROOT=Enter full MO2 folder path: "
)
if not defined MO2_ROOT exit /b 1
if not exist "%MO2_ROOT%\ModOrganizer.exe" (
 echo [ERROR] Not a valid MO2 folder. ModOrganizer.exe was not found.
 echo Expected path: %MO2_ROOT%\ModOrganizer.exe
 pause
 exit /b 1
)
if not exist "%MO2_ROOT%\plugins" mkdir "%MO2_ROOT%\plugins"
if errorlevel 1 (
 echo [ERROR] Cannot create the plugins folder.
 pause
 exit /b 1
)
set "DEST=%MO2_ROOT%\plugins\mo2_update_arsenal"
set "BACKUP="
if exist "%DEST%" (
 echo Found an older Update Arsenal plugin. Keeping a backup beside it.
 for /f "tokens=1-3 delims=/-. " %%A in ("%DATE%") do set "STAMP=%%C%%A%%B"
 if not defined STAMP set "STAMP=previous"
 set "BACKUP=%MO2_ROOT%\plugins\mo2_update_arsenal_backup_%RANDOM%%RANDOM%"
 move "%DEST%" "%BACKUP%" >nul
 if errorlevel 1 (
  echo [ERROR] Could not back up older plugin. Close MO2 and retry.
  pause
  exit /b 1
 )
)
xcopy "%SOURCE%\*" "%DEST%\" /E /I /H /Y /Q >nul
if errorlevel 1 (
 echo [ERROR] Copy failed. Restoring previous plugin if available.
 goto :rollback
)
if not exist "%DEST%\web\index.html" (
 echo [ERROR] The copy is incomplete. Restoring previous plugin if available.
 goto :rollback
)
echo.
echo [SUCCESS] MO2 UPDATE ARSENAL installed into:
echo %DEST%
echo.
echo Restart MO2, then Tools ^> Update Arsenal - Quest Board.
echo Nexus sign-in: MO2 Settings ^> Nexus ^> Connect to Nexus.
echo.
pause
exit /b 0
:rollback
if exist "%DEST%" rmdir /s /q "%DEST%"
if defined BACKUP if exist "%BACKUP%" move "%BACKUP%" "%DEST%" >nul
pause
exit /b 1
