@echo off
chcp 65001 >nul
title IPTV 本地一键测速与自动推送
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0一键测速并推送.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] 执行过程中出现异常，退出代码：%ERRORLEVEL%
    pause
)
