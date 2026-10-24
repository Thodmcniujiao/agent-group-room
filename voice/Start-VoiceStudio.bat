@echo off
chcp 65001 >nul
title Voice Studio - AI Girls TTS
cd /d "%~dp0"
set PYTHON_EXE=..\.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtualenv Python not found: %PYTHON_EXE%
    echo Please run Install.ps1 in the project root first.
    pause
    exit /b 1
)

echo =====================================================
echo   Starting Voice Studio...
echo   Open: http://127.0.0.1:5005
echo =====================================================

start "" http://127.0.0.1:5005
"%PYTHON_EXE%" server.py --port 5005
pause
