@echo off
title Partner Installer
color 0B

echo.
echo === Partner Installer ===
echo.

:: check python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during install.
    echo After installing, run this installer again.
    echo.
    pause
    exit /b
)

for /f "tokens=2 delims= " %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER%

:: check pip
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] pip not found. Installing...
    python -m ensurepip --upgrade
)
echo [OK] pip available

:: choose backend
echo.
echo Select AI backend (Partner needs one to process tasks):
echo.
echo   1 - Hermes Agent (recommended)
echo   2 - OpenClaw
echo   3 - Both
echo   4 - Skip, configure later
echo.
set /p AGENT=Choice [1-4] (default 1): 
if "%AGENT%"=="" set AGENT=1
if "%AGENT%"=="1" set AGENT=1
if "%AGENT%"=="2" set AGENT=2
if "%AGENT%"=="3" set AGENT=3
if "%AGENT%"=="4" set AGENT=4

:: install backend
if "%AGENT%"=="1" (
    echo.
    echo Installing Hermes Agent...
    python -m pip install hermes-agent -q
    if %errorlevel% equ 0 (
        echo [OK] Hermes Agent installed
    ) else (
        echo [WARN] Hermes install failed, you can try manually: pip install hermes-agent
    )
)
if "%AGENT%"=="2" (
    echo.
    echo Installing OpenClaw (requires Node.js)...
    where node >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Node.js not found.
        echo Download from: https://nodejs.org/
        pause
        exit /b
    )
    npm install -g openclaw@latest
    echo [OK] OpenClaw installed
)
if "%AGENT%"=="3" (
    echo.
    echo Installing Hermes Agent...
    python -m pip install hermes-agent -q
    if %errorlevel% equ 0 ( echo [OK] Hermes Agent installed ) else ( echo [WARN] Hermes install failed )
    echo.
    echo Installing OpenClaw...
    where node >nul 2>&1
    if %errorlevel% equ 0 (
        npm install -g openclaw@latest
        echo [OK] OpenClaw installed
    ) else (
        echo [WARN] Node.js not found. Install from https://nodejs.org/
    )
)
if "%AGENT%"=="4" (
    echo.
    echo Skipping backend install. You can install one later.
)

:: install partner
echo.
echo Installing Partner...
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if exist ".partner" (
    cd .partner
    git pull --ff-only >nul 2>&1
    cd ..
) else (
    where git >nul 2>&1
    if %errorlevel% equ 0 (
        git clone https://github.com/zty522/partner.git .partner >nul 2>&1
    ) else (
        echo [WARN] Git not found, using local files
    )
)

if exist ".partner" (
    cd .partner
    python -m pip install -e . -q
    if %errorlevel% equ 0 (
        echo [OK] Partner installed
    ) else (
        echo [ERROR] Partner install failed
        pause
        exit /b
    )
    cd ..
) else (
    python -m pip install -e . -q
    if %errorlevel% equ 0 (
        echo [OK] Partner installed
    ) else (
        echo [ERROR] Partner install failed
        pause
        exit /b
    )
)

:: create desktop shortcut
echo.
echo Creating desktop shortcut...
set SHORTCUT=%USERPROFILE%\Desktop\Partner.lnk
(
echo Set WshShell = CreateObject("WScript.Shell"^)
echo Set sc = WshShell.CreateShortcut("%SHORTCUT%"^)
echo sc.TargetPath = "powershell.exe"
echo sc.Arguments = "-NoExit -Command partner status"
echo sc.Description = "Partner AI Research Companion"
echo sc.WorkingDirectory = "%USERPROFILE%"
echo sc.Save
) > %TEMP%\mklnk.vbs
cscript //nologo %TEMP%\mklnk.vbs >nul
del %TEMP%\mklnk.vbs
echo [OK] Desktop shortcut created

echo.
echo ====================================
echo  INSTALLATION COMPLETE!
echo ====================================
echo.
echo  Open a new command prompt and type:
echo    partner status
echo.
echo  Or double-click the Partner icon on your desktop.
echo.
pause
