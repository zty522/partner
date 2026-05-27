@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0;%PYTHONPATH%
start "" pythonw.exe -m partner.gui