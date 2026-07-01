@echo off
title Partner
:: Partner Launcher - 快速单例检测，零Python开销
:: 放在 dist/Partner/ 目录下，用户双击此文件启动

:: 1. 极速检测 Partner.exe 是否已在运行
tasklist /nh /fi "IMAGENAME eq Partner.exe" 2>nul | findstr /i "Partner.exe" >nul
if %errorlevel% equ 0 (
    :: 已在运行 -> 用 PowerShell 激活窗口
    powershell -windowstyle hidden -Command "& {$wshell=New-Object -ComObject WScript.Shell; $wshell.AppActivate('Partner'); exit 0}"
    exit /b 0
)

:: 2. 启动
start "" "%~dp0Partner.exe"
exit /b 0
