@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is required. Install Python 3.10 or newer.
  pause
  exit /b 1
)
python server.py %*
if errorlevel 1 pause
