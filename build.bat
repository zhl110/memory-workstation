@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================
echo  memory-station Build Script
echo ========================================
echo.

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: PyInstaller not found. Run: pip install pyinstaller
    pause
    exit /b 1
)

set VERSION_FILE=__build_version.txt
set MAJOR=1
set MINOR=0
set PATCH=0
if exist "%VERSION_FILE%" (
    set /p VERSION=<"%VERSION_FILE%"
    for /f "tokens=1-3 delims=." %%a in ("!VERSION!") do (
        set MAJOR=%%a
        set MINOR=%%b
        set PATCH=%%c
    )
    set /a PATCH+=1
)
set NEW_VERSION=%MAJOR%.%MINOR%.%PATCH%
echo !NEW_VERSION! > "%VERSION_FILE%"
echo Building version: !NEW_VERSION!

echo.
echo [1/2] Cleaning temp build files...
if exist build rmdir /s /q build

echo [2/2] Building EXE...
python -m PyInstaller MemoryWorkstation.spec --noconfirm
if errorlevel 1 (
    echo ERROR: Build failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Build Complete! v%NEW_VERSION%
echo ========================================
echo  Output: dist\
echo.
pause