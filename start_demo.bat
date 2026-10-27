@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start_demo.py %*
) else (
  python start_demo.py %*
)
if errorlevel 1 pause
