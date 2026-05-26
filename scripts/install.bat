@echo off
title Partner Installer
color 0B

echo.
echo === Partner Installer ===
echo.

:: check python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Download from:
    echo https://www.python.org/downloads/
    echo (check "Add Python to PATH" during install)
    pause
    exit /b
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do echo [OK] Python %%i

:: check git
git --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] Git not found - will use local files
    set USE_ZIP=1
) else (
    echo [OK] Git found
    set USE_ZIP=0
)

:: choose backend
echo.
echo Choose AI backend:
echo   1 - Hermes Agent (recommended)
echo   2 - OpenClaw
echo   3 - Both
echo   4 - Skip, I'll configure later
echo.
set /p agent=Choice [1-4] (default 1): 
if "%agent%"=="" set agent=1

:: install backend
if "%agent%"=="1" (
    echo.
    echo Installing Hermes Agent...
    python -m pip install hermes-agent -q
    echo [OK] Hermes Agent installed
)
if "%agent%"=="2" (
    echo.
    echo Installing OpenClaw...
    where node >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Node.js not found. Get it from:
        echo https://nodejs.org/
        pause
        exit /b
    )
    npm install -g openclaw@latest
    echo [OK] OpenClaw installed
)
if "%agent%"=="3" (
    echo.
    echo Installing Hermes Agent...
    python -m pip install hermes-agent -q
    echo [OK] Hermes Agent installed
    echo.
    echo Installing OpenClaw...
    where node >nul 2>&1
    if not errorlevel 1 (
        npm install -g openclaw@latest
        echo [OK] OpenClaw installed
    ) else (
        echo [WARN] Node.js not found. Install manually from https://nodejs.org/
    )
)

:: install partner
echo.
echo Installing Partner...
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if "%USE_ZIP%"=="1" (
    python -m pip install -e . -q
) else (
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
echo [OK] Partner installed

:: create desktop shortcut
echo Creating desktop shortcut...
set SHORTCUT=%USERPROFILE%\Desktop\Partner.lnk
echo Set WshShell = CreateObject("WScript.Shell") > %TEMP%\mklnk.vbs
echo Set sc = WshShell.CreateShortcut("%SHORTCUT%") >> %TEMP%\mklnk.vbs
echo sc.TargetPath = "powershell.exe" >> %TEMP%\mklnk.vbs
echo sc.Arguments = "-NoExit -Command partner status" >> %TEMP%\mklnk.vbs
echo sc.Description = "Partner AI Research Companion" >> %TEMP%\mklnk.vbs
echo sc.WorkingDirectory = "%USERPROFILE%" >> %TEMP%\mklnk.vbs
echo sc.Save >> %TEMP%\mklnk.vbs
cscript //nologo %TEMP%\mklnk.vbs >nul
del %TEMP%\mklnk.vbs
echo [OK] Desktop shortcut created

echo.
echo ======================================================
echo  INSTALLATION COMPLETE!
echo ======================================================
echo.
echo  Open a NEW command prompt and type: partner status
echo  Or double-click the Partner shortcut on your desktop.
echo.
pause
