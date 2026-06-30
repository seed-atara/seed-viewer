@echo off
setlocal
title Seed Viewer - Update / Install
REM ===========================================================================
REM  Update Seed Viewer.bat  -  installs / updates Seed Viewer to the LATEST
REM  release. No admin rights needed. Just double-click it. Re-run any time to
REM  get the newest version. (For when the in-app auto-update isn't working.)
REM
REM  Needs Windows 10 (1803+) or Windows 11 - they include curl + tar already.
REM ===========================================================================

set "DEST=%LOCALAPPDATA%\Programs\SeedViewer"
set "URL=https://github.com/seed-atara/seed-viewer/releases/latest/download/SeedViewer-win.zip"
set "ZIP=%TEMP%\SeedViewer-win.zip"
set "STAGE=%TEMP%\sv_update_stage"
set "LOG=%TEMP%\seedviewer_update.log"

echo.
echo  ===========================================
echo    SEED VIEWER  -  update to latest version
echo  ===========================================
echo.
echo  Installing to:  %DEST%
echo.

echo  [1/5] Closing Seed Viewer if it's running...
taskkill /f /im SeedViewer.exe >nul 2>&1
REM let Windows release the .exe file handle before we overwrite it
ping -n 4 127.0.0.1 >nul

echo  [2/5] Downloading the latest build...
del /q "%ZIP%" 2>nul
curl -L -o "%ZIP%" "%URL%"
if errorlevel 1 (
  echo.
  echo   ERROR: download failed. Check your internet connection and try again.
  echo.
  pause & exit /b 1
)

echo  [3/5] Unpacking...
rmdir /s /q "%STAGE%" 2>nul
mkdir "%STAGE%"
tar -xf "%ZIP%" -C "%STAGE%"
if not exist "%STAGE%\SeedViewer\SeedViewer.exe" (
  echo.
  echo   ERROR: the download looks incomplete ^(no SeedViewer.exe^). Please re-run.
  echo.
  pause & exit /b 1
)

echo  [4/5] Installing...
if not exist "%DEST%" mkdir "%DEST%"
REM robocopy /MIR mirrors file-by-file, retries locked files, and works in place -
REM far more reliable than delete-the-folder-then-move (which AV / open Explorer
REM windows often block). Exit codes 0-7 = success, 8+ = real failure.
robocopy "%STAGE%\SeedViewer" "%DEST%" /MIR /R:5 /W:2 /NFL /NDL /NJH /NJS >"%LOG%" 2>&1
if %ERRORLEVEL% GEQ 8 (
  echo.
  echo   ERROR: install failed. Details in: %LOG%
  echo   Close any Explorer window showing the SeedViewer folder, then re-run.
  echo.
  pause & exit /b 1
)
rmdir /s /q "%STAGE%" 2>nul
del /q "%ZIP%" 2>nul

echo  [5/5] Refreshing the Desktop shortcut...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(([Environment]::GetFolderPath('Desktop'))+'\Seed Viewer.lnk'); $s.TargetPath='%DEST%\SeedViewer.exe'; $s.WorkingDirectory='%DEST%'; $s.Save()" >nul 2>&1

echo.
echo  ===========================================
echo    Done!  Launching Seed Viewer...
echo  ===========================================
echo  Use the "Seed Viewer" shortcut on your Desktop from now on.
echo  Re-run this file any time to update to the newest version.
echo.
start "" "%DEST%\SeedViewer.exe"
timeout /t 6 >nul
endlocal
