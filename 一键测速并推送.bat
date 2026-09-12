@echo off
chcp 65001 >nul
title 🚀 IPTV 自动化精准测速与云端全量同步
cd /d "%~dp0"

echo =====================================================================
echo  正在启动 IPTV 真实网络环境测速与全频道源优选同步程序...
echo =====================================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PY_CMD=py"
    ) else (
        echo ❌ 未检测到 Python 环境，请先安装 Python 并添加到 PATH 环境变量。
        echo.
        pause
        exit /b 1
    )
)

%PY_CMD% -X utf8 run_update.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo ❌ 运行出现异常，错误代码: %ERRORLEVEL%
)

echo.
pause
