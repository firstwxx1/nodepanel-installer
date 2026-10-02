@echo off
chcp 65001 >nul
setlocal

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.8+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

if not exist "%~dp0install.py" (
  echo 缺少 install.py，请先下载完整仓库：
  echo   https://github.com/firstwxx1/nodepanel-installer/archive/refs/heads/main.zip
  pause
  exit /b 1
)

cd /d "%~dp0"
python install.py %*
echo.
pause
