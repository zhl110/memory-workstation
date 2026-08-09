@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================
echo  Memory Workstation Build Script
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
echo [1/3] Cleaning temp build files...
if exist build rmdir /s /q build

echo [2/3] Building EXE...
python -m PyInstaller MemoryWorkstation.spec --noconfirm
if errorlevel 1 (
    echo ERROR: Build failed!
    pause
    exit /b 1
)

REM ---- Local version: D:\MemoryWorkstation_v{version}\ ----
set LOCAL_DIR=D:\MemoryWorkstation\MemoryWorkstation_v%NEW_VERSION%
if exist "%LOCAL_DIR%" rmdir /s /q "%LOCAL_DIR%"
mkdir "%LOCAL_DIR%"

if exist "dist\MemoryWorkstation.exe" (
    move /y "dist\MemoryWorkstation.exe" "%LOCAL_DIR%\MemoryWorkstation.exe" >nul 2>&1
) else (
    if exist "dist\MemoryWorkstation" (
        move /y "dist\MemoryWorkstation" "%LOCAL_DIR%" >nul 2>&1
    )
)
echo {"version": "%NEW_VERSION%", "build_date": "%DATE%"} > "%LOCAL_DIR%\version.json"
echo D:\MemoryWorkstation\.memory-workstation> "%LOCAL_DIR%\MemoryWorkstation.cfg"
echo Local version: %LOCAL_DIR%

REM ---- Public version: dist\MemoryWorkstation_v{version}_public\ ----
set PUBLIC_DIR=dist\MemoryWorkstation_v%NEW_VERSION%_public
if exist "%PUBLIC_DIR%" rmdir /s /q "%PUBLIC_DIR%"
mkdir "%PUBLIC_DIR%"

copy /y "%LOCAL_DIR%\MemoryWorkstation.exe" "%PUBLIC_DIR%\MemoryWorkstation.exe" >nul 2>&1
copy /y "%LOCAL_DIR%\version.json" "%PUBLIC_DIR%\version.json" >nul 2>&1
echo This is a PUBLIC BUILD of Memory Workstation v%NEW_VERSION%. > "%PUBLIC_DIR%\README.txt"
echo. >> "%PUBLIC_DIR%\README.txt"
echo Public Build / Gongkai Ban - contains no local data. >> "%PUBLIC_DIR%\README.txt"
echo On first run it will auto-initialize (empty storage, random token). >> "%PUBLIC_DIR%\README.txt"
echo Public version: %PUBLIC_DIR%

echo.
echo [3/3] Updating desktop shortcut to latest local version...
powershell -Command ^
    $WS = New-Object -ComObject WScript.Shell; ^
    $SC = $WS.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\MemoryWorkstation.lnk'); ^
    $SC.TargetPath = '%LOCAL_DIR%\MemoryWorkstation.exe'; ^
    $SC.WorkingDirectory = '%LOCAL_DIR%'; ^
    $SC.Description = 'Memory Workstation v%NEW_VERSION%'; ^
    $SC.Save()

echo.
echo ========================================
echo  Build Complete! v%NEW_VERSION%
echo ========================================
echo  Local: %LOCAL_DIR%
echo  Public: %PUBLIC_DIR%
echo  Desktop shortcut points to: %LOCAL_DIR%\MemoryWorkstation.exe
echo.
for /d %%d in (D:\MemoryWorkstation\MemoryWorkstation_v*) do echo    Local: %%d
echo.
pause
