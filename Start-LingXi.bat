@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    py -3 -B desktop.py
    if errorlevel 1 pause
    exit /b
)
python -B desktop.py
if errorlevel 1 pause
