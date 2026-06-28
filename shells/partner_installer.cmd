@echo off
chcp 65001 >nul
title Partner Setup

echo ============================================
echo   🤝 Partner - AI Research Companion
echo   Setup Wizard
echo ============================================
echo.

:: Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
set "PARTNER_EXE=%SCRIPT_DIR%Partner.exe"

if not exist "%PARTNER_EXE%" (
    echo [ERROR] Partner.exe not found alongside this installer.
    echo        Please place both files in the same folder.
    pause
    exit /b 1
)

echo Step 1: Choose installation directory
echo -------------------------------------
set "DEFAULT_INSTALL_DIR=C:\Program Files\Partner"
echo Default: %DEFAULT_INSTALL_DIR%
set /p "INSTALL_DIR=Install directory [%DEFAULT_INSTALL_DIR%]: "
if "%INSTALL_DIR%"=="" set "INSTALL_DIR=%DEFAULT_INSTALL_DIR%"

:: Create installation directory
mkdir "%INSTALL_DIR%" 2>nul
if not exist "%INSTALL_DIR%" (
    echo [ERROR] Cannot create directory: %INSTALL_DIR%
    echo         Try running as Administrator.
    pause
    exit /b 1
)

echo.
echo Step 2: Choose workspace directory
echo ------------------------------------
set "DEFAULT_WS_DIR=%USERPROFILE%\partner_workspace"
echo Default: %DEFAULT_WS_DIR%
set /p "WS_DIR=Workspace directory [%DEFAULT_WS_DIR%]: "
if "%WS_DIR%"=="" set "WS_DIR=%DEFAULT_WS_DIR%"

:: Create workspace directory structure
mkdir "%WS_DIR%\config" 2>nul
mkdir "%WS_DIR%\instances" 2>nul

echo.
echo Step 3: Copying files...
echo -------------------------
copy /Y "%PARTNER_EXE%" "%INSTALL_DIR%\Partner.exe" >nul
if %errorlevel% neq 0 (
    echo [ERROR] Failed to copy Partner.exe to %INSTALL_DIR%
    pause
    exit /b 1
)
echo   ✅ Partner.exe copied to %INSTALL_DIR%

:: Write workspace pointer file
echo %WS_DIR% > "%USERPROFILE%\.partner_workspace"
echo   ✅ Workspace pointer written: %%USERPROFILE%%\.partner_workspace

:: Create desktop shortcut
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Partner.lnk"
if exist "%SHORTCUT_PATH%" del "%SHORTCUT_PATH%"
mshta "javascript:var s=new ActiveXObject('WScript.Shell');var l=s.CreateShortcut('%SHORTCUT_PATH%');l.TargetPath='%INSTALL_DIR%\Partner.exe';l.WorkingDirectory='%INSTALL_DIR%';l.Description='Partner AI Research Companion';l.Save();close();" 2>nul
echo   ✅ Desktop shortcut created

echo.
echo ============================================
echo   ✅ Installation complete!
echo   Partner has been installed to:
echo     %INSTALL_DIR%
echo   Workspace:
echo     %WS_DIR%
echo ============================================
echo.
echo Press any key to start Partner...
pause >nul
start "" "%INSTALL_DIR%\Partner.exe"
