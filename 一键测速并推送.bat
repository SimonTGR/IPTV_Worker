@echo off
title IPTV Auto SpeedTest and Push
cd /d "%~dp0"
python -X utf8 run_update.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo Execution failed with code: %ERRORLEVEL%
    pause
)
