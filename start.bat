@echo off
cd /d "%~dp0"
title File Locator Tool

REM Check Python
python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12+ and add to PATH.
    pause
    exit /b 1
)

REM Check libs exists
if not exist "libs" (
    echo [ERROR] libs/ not found.
    echo         Run: pip install --target libs -r requirements.txt
    pause
    exit /b 1
)

REM Launch (app.py injects libs into sys.path automatically)
python -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo [!] App stopped.
)
pause
