@echo off
REM STB Remote Control App - PyInstaller 빌드
REM stb-rpa 폴더에서 실행: build_remote_control_exe.bat

cd /d "%~dp0"

python -c "import PyInstaller" 2>nul || (
  echo PyInstaller not found. Install with: pip install pyinstaller
  exit /b 1
)

python -m PyInstaller --clean remote_control_app.spec
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

echo.
echo Build complete. Exe: dist\STB_Remote_Control.exe
pause
