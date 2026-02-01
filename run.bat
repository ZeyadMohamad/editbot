@echo off
:: EditBot Quick Launcher
:: Double-click to run, or drag-and-drop a video file onto this

cd /d "%~dp0"

:: Activate virtual environment
call .venv\Scripts\activate.bat

:: Run with any arguments passed
if "%~1"=="" (
    python run.py
) else (
    python run.py %*
)

pause
