@echo off
chcp 65001 >nul
title Partner 🤝 安装程序
color 0B

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║         🤝 Partner 安装程序             ║
echo   ║    Your AI Research Companion            ║
echo   ╚══════════════════════════════════════════╝
echo.

:: ── 检查 Python ──
echo  [1/4] 检查 Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠ 未检测到 Python
    echo  即将打开 Python 下载页面，请下载并安装
    start https://www.python.org/downloads/
    echo  安装时记得勾选 "Add Python to PATH"
    echo  安装完成后重新运行本程序
    pause
    exit /b
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set pyver=%%i
echo  ✓ Python %pyver%

:: ── 检查 Git (可选) ──
echo  [2/4] 检查 Git...
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠ Git 未安装，将使用 ZIP 安装
    set USE_ZIP=1
) else (
    echo  ✓ Git 已安装
    set USE_ZIP=0
)

:: ── 选择 AI 后端 ──
echo.
echo  [3/4] 选择 AI 后端
echo.
echo    Partner 需要一个 AI 后端来处理研究和对话。
echo.
echo    1) Hermes Agent（推荐）
echo    2) OpenClaw（小龙蝦）
echo    3) 两者都装
echo    4) 暂不安装
echo.
set /p agent_choice="  请输入 (1-4，默认 1): "
if "%agent_choice%"=="" set agent_choice=1

:: ── 安装选中的后端 ──
if "%agent_choice%"=="1" goto install_hermes
if "%agent_choice%"=="2" goto install_openclaw
if "%agent_choice%"=="3" goto install_both
if "%agent_choice%"=="4" goto install_partner
goto install_hermes

:install_hermes
echo.
echo  正在安装 Hermes Agent...
python -m pip install hermes-agent -q
echo  ✓ Hermes Agent 安装完成
goto install_partner

:install_openclaw
echo.
echo  正在安装 OpenClaw...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠ 需要 Node.js，打开下载页面...
    start https://nodejs.org/
    echo  安装 Node.js 后重新运行本程序
    pause
    exit /b
)
npm install -g openclaw@latest
echo  ✓ OpenClaw 安装完成
goto install_partner

:install_both
echo.
echo  正在安装 Hermes Agent...
python -m pip install hermes-agent -q
echo  ✓ Hermes Agent 安装完成
echo.
echo  正在安装 OpenClaw...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠ 需要 Node.js，打开下载页面...
    start https://nodejs.org/
    echo  安装 Node.js 后重新运行本程序，OpenClaw 可手动安装
) else (
    npm install -g openclaw@latest
    echo  ✓ OpenClaw 安装完成
)
goto install_partner

:: ── 安装 Partner ──
:install_partner
echo.
echo  [4/4] 安装 Partner...

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if "%USE_ZIP%"=="1" (
    echo  ✓ 使用本地文件安装
    python -m pip install -e . -q
) else (
    echo  正在从 GitHub 克隆...
    if exist ".partner" (
        cd .partner
        git pull --ff-only >nul 2>&1
        cd ..
    ) else (
        git clone https://github.com/zty522/partner.git .partner >nul 2>&1
    )
    cd .partner
    python -m pip install -e . -q
)

:: ── 创建快捷方式 ──
echo.
echo  正在创建快捷方式...
set SHORTCUT_DIR=%USERPROFILE%\Desktop
set SHORTCUT_PATH=%SHORTCUT_DIR%\Partner.lnk
if exist "%SHORTCUT_DIR%" (
    >"%TEMP%\create_shortcut.vbs" echo Set WshShell = CreateObject("WScript.Shell"^)
    >>"%TEMP%\create_shortcut.vbs" echo Set Shortcut = WshShell.CreateShortcut("%SHORTCUT_PATH%"^)
    >>"%TEMP%\create_shortcut.vbs" echo Shortcut.TargetPath = "powershell.exe"
    >>"%TEMP%\create_shortcut.vbs" echo Shortcut.Arguments = "-NoExit -Command partner status"
    >>"%TEMP%\create_shortcut.vbs" echo Shortcut.Description = "Partner - Your AI Research Companion"
    >>"%TEMP%\create_shortcut.vbs" echo Shortcut.WorkingDirectory = "%USERPROFILE%"
    >>"%TEMP%\create_shortcut.vbs" echo Shortcut.Save
    cscript //nologo "%TEMP%\create_shortcut.vbs" >nul
    del "%TEMP%\create_shortcut.vbs"
    echo  ✓ 桌面快捷方式已创建
)

:: ── 完成 ──
echo.
echo   ╔══════════════════════════════════════════╗
echo   ║       🎉 安装完成！                      ║
echo   ╚══════════════════════════════════════════╝
echo.
echo   安装目录: %SCRIPT_DIR%.partner
echo.
echo   使用方法:
echo     新开命令行，直接输入 partner 即可
echo     或双击桌面的 Partner 快捷方式
echo.
echo   首次使用:
echo     partner setup        配置向导
echo     partner status       查看状态
echo     partner bot start qq 启动 QQ 机器人
echo.
pause
