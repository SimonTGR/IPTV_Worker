@echo off
chcp 65001 >nul
title IPTV 本地网络精准测速与自动云端同步
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PY_CMD=py"
    ) else (
        echo [ERROR] 未检测到 Python 环境，请确保已安装 Python 并勾选 Add to PATH。
        echo.
        pause
        exit /b 1
    )
)

%PY_CMD% -X utf8 run_update.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] 运行过程中出现错误，退出码: %ERRORLEVEL%
)

echo.
pause
