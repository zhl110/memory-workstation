@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM =============================================
REM MW Core Engine: C++ builder
REM Requirements:
REM   - MSVC Build Tools (vcvarsall.bat)
REM   - CMake 3.15+
REM   - pybind11
REM   - Python 3.13+
REM   - onnxruntime (third_party/onnxruntime, with include/ + lib/)
REM   - embedding model (third_party/models/*.onnx) when embedding needed
REM =============================================

REM ---- Environment: override by env vars, else use reasonable defaults ----
set "VCVARS=%VCVARS%"
if not defined VCVARS (
    if defined VSINSTALLDIR (
        set "VCVARS=!VSINSTALLDIR!\VC\Auxiliary\Build\vcvarsall.bat"
    ) else (
        set "VCVARS=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
    )
)

set "PYTHON=%MW_PYTHON%"
if not defined PYTHON set "PYTHON=python"

set SRC_DIR=%~dp0
set BUILD_DIR=%~dp0build

echo === MW Core C++ Build ===
echo Source: %SRC_DIR%
echo Build:  %BUILD_DIR%
echo Python: %PYTHON%
echo.

if not exist "%SRC_DIR%third_party\onnxruntime\include" (
    echo [WARN] onnxruntime not found in third_party\onnxruntime.
    echo        C++ engine requires onnxruntime runtime. Either:
    echo        - download and place it under third_party/onnxruntime, or
    echo        - use the pure-Python fallback (SDK works without C++ engine).
    echo.
)

REM 用临时文件获取 pybind11 cmake 目录（避免 for /f 空格问题）
set "PYBIND11_TMP=%TEMP%\mw_pybind11_dir.txt"
"%PYTHON%" -c "import pybind11; print(pybind11.get_cmake_dir())" > "%PYBIND11_TMP%" 2>&1
set /p PYBIND11_DIR=<"%PYBIND11_TMP%"
del "%PYBIND11_TMP%" 2>nul
if errorlevel 1 (
    echo [ERROR] pybind11 not found. Run: pip install pybind11
    exit /b 1
)
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

set "PYD_FILE=%BUILD_DIR%\mw_core.cp*-win_amd64.pyd"
for /f "delims=" %%f in ('dir /b "%BUILD_DIR%\mw_core.cp*-win_amd64.pyd" 2^>nul') do set "PYD_FILE=%BUILD_DIR%\%%f"
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