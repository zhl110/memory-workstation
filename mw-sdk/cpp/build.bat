@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set VCVARS=D:\VS_BuildTools\VS_BuildTools\2026_Insiders\VC\Auxiliary\Build\vcvarsall.bat
set SRC_DIR=%~dp0
set BUILD_DIR=%~dp0build
set "PYTHON=D:\py313\python.exe"

echo === MW Core C++ Build ===
echo Source: %SRC_DIR%
echo Build:  %BUILD_DIR%
echo Python: %PYTHON%

REM 用临时文件获取 pybind11 cmake 目录（避免 for /f 空格问题）
set "PYBIND11_TMP=%TEMP%\mw_pybind11_dir.txt"
"%PYTHON%" -c "import pybind11; print(pybind11.get_cmake_dir())" > "%PYBIND11_TMP%" 2>&1
set /p PYBIND11_DIR=<"%PYBIND11_TMP%"
del "%PYBIND11_TMP%" 2>nul
echo pybind11: !PYBIND11_DIR!
echo.

if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
mkdir "%BUILD_DIR%"

call "%VCVARS%" x64 >nul 2>&1
cd /d "%BUILD_DIR%"
cmake -G "NMake Makefiles" -Dpybind11_DIR="!PYBIND11_DIR!" -DPYBIND11_FINDPYTHON=ON -DPython_EXECUTABLE="%PYTHON%" "%SRC_DIR%"
if errorlevel 1 (
    echo [ERROR] CMake failed
    exit /b 1
)

cmake --build .
if errorlevel 1 (
    echo [ERROR] Build failed
    exit /b 1
)

set "PYD_FILE=%BUILD_DIR%\mw_core.cp313-win_amd64.pyd"
set "DEST=%~dp0..\mw_sdk\_core"
if exist "!PYD_FILE!" (
    copy /y "!PYD_FILE!" "!DEST!" >nul
    echo.
    echo [OK] Build success, copied to !DEST!
) else (
    echo [ERROR] .pyd file not found
    exit /b 1
)

endlocal
