@echo off
:: Partner post-install script — called by installer after files are copied
cd /d "%~dp0"

echo.
echo Completing Partner setup...
echo.

:: Install Partner Python package
echo [1/2] Installing Partner package...
python -m pip install -e "%CD%" -q
if %errorlevel% neq 0 (
    echo [WARN] pip install failed, try: python -m pip install -e "%CD%" --break-system-packages
)

:: Add to PATH
echo [2/2] Adding to PATH...
powershell -Command "$p = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($p -notlike '*Partner*') { [Environment]::SetEnvironmentVariable('Path', '%CD%;' + $p, 'User'); Write-Output 'PATH updated' } else { Write-Output 'Already in PATH' }"

echo.
echo ====================================
echo  Partner installed successfully!
echo ====================================
echo.
echo  Create a shortcut on your desktop:
echo    Target: wscript.exe "%CD%\Partner.vbs"
echo    Start in: "%CD%"
echo.
echo  Or double-click Partner.vbs directly to launch.
echo.
