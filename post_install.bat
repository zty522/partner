@echo off
:: Partner post-install script
:: Called by the installer after files are copied
cd /d "%~dp0"

echo.
echo Completing Partner setup...
echo.

:: Install Partner Python package
echo [1/3] Installing Partner package...
python -m pip install -e "%CD%" -q
if %errorlevel% neq 0 (
    echo [WARN] pip install failed, trying alternative...
    python -m pip install -e "%CD%\partner" -q
)

:: Add to PATH
echo [2/3] Adding to PATH...
set TARGET=%USERPROFILE%\AppData\Local\Microsoft\WindowsApps
powershell -Command "$p = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($p -notlike '*Partner*') { [Environment]::SetEnvironmentVariable('Path', \"%CD%;$p\", 'User'); Write-Output 'PATH updated' } else { Write-Output 'Already in PATH' }"

:: Create startup script
echo [3/3] Creating launcher...
set LAUNCHER=%CD%\Partner.exe
(
echo @echo off
echo cd /d "%%~dp0"
echo python -m partner.cli status
echo pause
) > "%LAUNCHER%"

echo.
echo ====================================
echo  Setup complete!
echo ====================================
echo.
echo  Open a new command prompt and type: partner status
echo  Or launch Partner from the Start Menu.
echo.
