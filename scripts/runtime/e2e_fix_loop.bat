@echo off
REM One-click auto-fix loop for Partner (WSL host).
REM Runs e2e_regression; on failure applies registered fix rules and re-verifies.
setlocal
wsl -e bash -c "/home/os/miniconda3/bin/python /mnt/e/work/partner/scripts/runtime/e2e_fix_loop.py --max-rounds 3 --timeout-min 55"
echo.
echo FIX_RC=%ERRORLEVEL%
endlocal
