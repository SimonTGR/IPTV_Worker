@echo off
chcp 65001 >nul
title 🚀 IPTV 自动化精准测速与云端全量同步
cd /d "%~dp0"

echo =====================================================================
echo  正在启动 IPTV 真实网络环境测速与全频道源优选同步程序...
echo =====================================================================
echo.

python -X utf8 run_update.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo ❌ 运行出现异常，错误代码: %ERRORLEVEL%
)

echo.
pause

