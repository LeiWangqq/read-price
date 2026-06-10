@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Setup - File Locator Tool

REM -- Check Python 3.12 --
set PYTHON_CMD=

py -3.12 --version >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    goto :found
)

python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Please install Python 3.12 (64-bit): https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo %PYVER% | findstr "3.12" >nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :found
)

echo [ERROR] Current Python: %PYVER%
echo         This tool requires Python 3.12 (64-bit).
echo         Download: https://www.python.org/downloads/
pause
exit /b 1

:found
echo [i] Using: %PYTHON_CMD%
%PYTHON_CMD% --version

REM -- Check 64-bit --
%PYTHON_CMD% -c "import struct; exit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] 64-bit Python required, but 32-bit detected.
    echo         Please install Python 3.12 64-bit:
    echo         https://www.python.org/downloads/
    echo         Download "Windows installer (64-bit)".
    pause
    exit /b 1
)

REM -- Remove old libs if exists --
if exist "libs" (
    echo [i] Removing old libs/ ...
    rmdir /s /q "libs"
)

REM -- Install dependencies (binary only, no source builds) --
echo.
echo [i] Installing dependencies into libs/ ...
echo     This may take a few minutes on first run.
echo.
%PYTHON_CMD% -m pip install --target libs --only-binary :all: -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed.
    echo         Make sure pip is available: %PYTHON_CMD% -m ensurepip
    echo         Make sure you have 64-bit Python 3.12.
    pause
    exit /b 1
)

echo.
echo [OK] Setup complete.
echo     Run start.bat to launch the application.
pause
