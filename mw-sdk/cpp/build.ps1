#!/usr/bin/env pwsh
# MW Core C++ Build Script

$ErrorActionPreference = "Stop"

# 设置控制台编码为 UTF-8，避免中文乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 > $null

$SrcDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildDir = Join-Path $SrcDir "build"
$Python = if ($env:MW_PYTHON) { $env:MW_PYTHON } else { "python" }
$Vcvars = $env:VCVARS
if (-not $Vcvars) {
    if ($env:VSINSTALLDIR) { $Vcvars = Join-Path $env:VSINSTALLDIR "VC\Auxiliary\Build\vcvarsall.bat" }
    else { $Vcvars = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" }
}

Write-Host "=== MW Core C++ Build ==="
Write-Host "Source: $SrcDir"
Write-Host "Build:  $BuildDir"
Write-Host "Python: $Python"

if (-not (Test-Path (Join-Path $SrcDir "third_party\onnxruntime\include"))) {
    Write-Host "[WARN] onnxruntime not found in third_party\onnxruntime." -ForegroundColor Yellow
    Write-Host "       C++ engine requires onnxruntime. Download and place it under third_party/onnxruntime," -ForegroundColor Yellow
    Write-Host "       or use the pure-Python fallback (SDK works without C++ engine)." -ForegroundColor Yellow
    Write-Host ""
}

# Get pybind11 cmake dir
$Pybind11Dir = & "$Python" -c "import pybind11; print(pybind11.get_cmake_dir())"
Write-Host "pybind11: $Pybind11Dir"
Write-Host ""

# Clean build dir
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

# Setup MSVC environment and run cmake
$env:CC = "cl.exe"
$env:CXX = "cl.exe"

# Call vcvarsall to set up the environment, then run cmake
cmd /c "`"$Vcvars`" x64 >nul 2>&1 && cmake -B `"$BuildDir`" -S `"$SrcDir`" -G `"NMake Makefiles`" -Dpybind11_DIR=`"$Pybind11Dir`" -DPYBIND11_FINDPYTHON=ON -DPython_EXECUTABLE=`"$Python`""
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] CMake configure failed" -ForegroundColor Red
    exit 1
}

cmd /c "`"$Vcvars`" x64 >nul 2>&1 && cmake --build `"$BuildDir`""
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Build failed" -ForegroundColor Red
    exit 1
}

# Copy pyd
$PydFile = Join-Path $BuildDir "mw_core.cp313-win_amd64.pyd"
$Dest = Join-Path $SrcDir "..\mw_sdk\_core"

if (Test-Path $PydFile) {
    Copy-Item -Path $PydFile -Destination $Dest -Force
    Write-Host ""
    Write-Host "[OK] Build success, copied to $Dest" -ForegroundColor Green
} else {
    Write-Host "[ERROR] .pyd file not found at $PydFile" -ForegroundColor Red
    exit 1
}
