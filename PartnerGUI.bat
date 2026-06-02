@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0;%PYTHONPATH%
set "PARTNER_PYTHONW="

if exist "C:\Python314\pythonw.exe" set "PARTNER_PYTHONW=C:\Python314\pythonw.exe"
if not defined PARTNER_PYTHONW if exist "%LocalAppData%\Programs\Python\Python314\pythonw.exe" set "PARTNER_PYTHONW=%LocalAppData%\Programs\Python\Python314\pythonw.exe"
if not defined PARTNER_PYTHONW for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PARTNER_PYTHONW set "PARTNER_PYTHONW=%%I"

if not defined PARTNER_PYTHONW (
    start "" python.exe -m partner.gui
    exit /b 1
)

start "" "%PARTNER_PYTHONW%" -m partner.gui
