@echo off
REM Local Windows build script
REM Usage: build\build_win.bat C:\path\to\seed-film

setlocal
set SEED_FILM=%~1
if "%SEED_FILM%"=="" (
    echo Usage: build\build_win.bat C:\path\to\seed-film
    exit /b 1
)

set REPO=%~dp0..
echo === Seed Viewer -- local Windows build ===
echo   seed-film: %SEED_FILM%
echo   seed-viewer: %REPO%
echo.

python "%REPO%\build\prepare_sources.py" --source "%SEED_FILM%"
if errorlevel 1 exit /b 1

python "%REPO%\build\download_ffmpeg.py"
if errorlevel 1 exit /b 1

cd /d "%REPO%"
pyinstaller build\seed_viewer_win.spec --distpath build\dist --workpath build\work --clean
if errorlevel 1 exit /b 1

echo.
echo === Build complete ===
echo Folder: %REPO%\build\dist\SeedViewer\
echo.
echo To zip:
echo   Compress-Archive -Path build\dist\SeedViewer -DestinationPath build\dist\SeedViewer-win.zip
