@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title File Locator Tool

REM -- Check for Python 3.12 --
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

echo [ERROR] Current Python version: %PYVER%
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
    echo [ERROR] 64-bit Python required, but 32-bit detected.
    echo         Please install Python 3.12 64-bit:
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

REM -- Check libs exists --
if not exist "libs" (
    echo.
    echo [!] libs/ not found. Running setup...
    echo.
    %PYTHON_CMD% -m pip install --target libs --only-binary :all: -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Setup failed. Run setup.bat manually.
        pause
        exit /b 1
    )
)

REM -- Check if libs/ is compatible with current Python --
%PYTHON_CMD% -c "import sys; sys.path.insert(0,'libs'); import pydantic_core" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [!] libs/ is not compatible with this Python version.
    echo     Rebuilding libs/ ...
    echo.
    rmdir /s /q "libs"
    %PYTHON_CMD% -m pip install --target libs --only-binary :all: -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Setup failed. Run setup.bat manually.
        pause
        exit /b 1
    )
    echo [OK] libs/ rebuilt successfully.
)

REM -- Prepend libs/ to PYTHONPATH --
set "PYTHONPATH=%cd%\libs;%cd%\libs\site-packages;%PYTHONPATH%"

echo [i] Starting...
%PYTHON_CMD% -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo [!] App exited with error.
)
pause
