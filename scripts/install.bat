@echo off
:: Partner Installer for Windows
:: Run this from the Partner directory after extracting the ZIP
cd /d "%~dp0"

echo.
echo ================================
echo  Partner - AI Research Companion
echo ================================
echo.

:: check python
echo [CHECK] Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found.
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER%

:: check pip
echo [CHECK] pip...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] pip not found, installing...
    python -m ensurepip --upgrade
)
echo [OK] pip available

:: install partner
echo.
echo Installing Partner...
set SCRIPT_DIR=%~dp0

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
    python -m pip install -e . -q --break-system-packages >nul 2>&1
    if %errorlevel% neq 0 (
        python -m pip install -e . -q >nul 2>&1
    )
    if %errorlevel% equ 0 (
        echo [OK] Partner installed
    ) else (
        echo [ERROR] Partner install failed
        pause
        exit /b
    )
    cd ..
) else (
    python -m pip install -e . -q --break-system-packages >nul 2>&1
    if %errorlevel% neq 0 (
        python -m pip install -e . -q >nul 2>&1
    )
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
echo sc.TargetPath = "wscript.exe"
echo sc.Arguments = "%~dp0Partner.vbs"
echo sc.Description = "Partner AI Research Companion"
echo sc.WorkingDirectory = "%~dp0"
echo sc.IconLocation = "%~dp0Partner.exe, 0"
echo sc.Save
) > %TEMP%\mklnk.vbs
cscript //nologo %TEMP%\mklnk.vbs >nul
del %TEMP%\mklnk.vbs
echo [OK] Desktop shortcut created

echo.
echo ================================
echo  Partner installed!
echo ================================
echo.
echo  Double-click the Partner icon on your desktop.
echo.
