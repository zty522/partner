@echo off
REM One-click end-to-end regression for Partner (WSL host).
REM Usage: e2e_regression.bat [max_rounds]
setlocal
set MAXR=%1
if "%MAXR%"=="" set MAXR=3
wsl -e bash -c "/home/os/miniconda3/bin/python /mnt/e/work/partner/scripts/runtime/e2e_regression.py --max-rounds %MAXR% --timeout-min 55"
echo.
echo E2E_RC=%ERRORLEVEL%
endlocal
