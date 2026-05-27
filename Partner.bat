@echo off
set PYTHONPATH=%~dp0;%PYTHONPATH%
if "%1"=="" (
    pythonw.exe -m partner.gui
) else (
    python -m partner.cli %*
)
