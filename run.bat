@echo off
rem Start PC Benchmark Manager (double-click this file).
rem The console window shows the database path and any errors; closing it closes the app.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The .venv environment is missing. Create it once from this folder:
    echo     py -3.10 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\main.py
if errorlevel 1 pause
