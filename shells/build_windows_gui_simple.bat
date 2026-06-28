@echo off
setlocal
cd /d E:\work\partner

echo Installing partner package...
python -m pip install -e . >nul 2>&1

echo Installing PyInstaller...
python -m pip install pyinstaller >nul 2>&1

echo Building Partner.exe...
python -m PyInstaller scripts\partner_windows.spec --clean --distpath dist

if exist dist\Partner.exe (
  echo BUILD OK
  echo Copying to Desktop...
  copy /Y dist\Partner.exe C:\Users\zty12\Desktop\Partner.exe
  echo Done
) else (
  echo BUILD FAILED
  exit /b 1
)
