#!/usr/bin/env bash
# NodePanel 一键安装 —— Linux / macOS
#   curl -fsSL https://raw.githubusercontent.com/firstwxx1/nodepanel-installer/main/install.sh | bash
set -e

REPO="firstwxx1/nodepanel-installer"
RAW="https://raw.githubusercontent.com/${REPO}/main/install.py"

PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  echo "未找到 python3 / python，请先安装 Python 3.8+"
  exit 1
fi

TMP="$(mktemp -d)/install.py"
echo "获取安装脚本…"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$RAW" -o "$TMP"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP" "$RAW"
else
  echo "需要 curl 或 wget"
  exit 1
fi

"$PY" "$TMP" "$@"
