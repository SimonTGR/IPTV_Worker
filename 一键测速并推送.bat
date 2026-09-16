@echo off
title IPTV SpeedTest and Auto Sync
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PY_CMD=py"
    ) else (
        echo [ERROR] Python environment not detected. Please install Python and add to PATH.
        echo.
        pause
        exit /b 1
    )
)

%PY_CMD% -X utf8 run_update.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Process exited with error code: %ERRORLEVEL%
)

echo.
pause
