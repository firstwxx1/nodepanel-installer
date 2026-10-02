@echo off
chcp 65001 >nul
setlocal

set REPO=firstwxx1/nodepanel-installer
set RAW=https://raw.githubusercontent.com/%REPO%/main/install.py

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.8+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

if not exist "%~dp0install.py" (
  echo 获取安装脚本...
  curl -fsSL -o "%~dp0install.py" "%RAW%"
  if errorlevel 1 (
    echo 下载失败，可手动从 https://raw.githubusercontent.com/%REPO%/main/install.py 下载
    pause
    exit /b 1
  )
)

python "%~dp0install.py" %*
echo.
pause
