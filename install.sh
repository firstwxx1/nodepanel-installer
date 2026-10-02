#!/usr/bin/env bash
# NodePanel 一键安装 —— Linux / macOS
#
# 推荐（无需 raw.githubusercontent）：
#   git clone https://github.com/firstwxx1/nodepanel-installer.git && cd nodepanel-installer && bash install.sh
#
# 或者只用 zip：
#   curl -fsSL https://github.com/firstwxx1/nodepanel-installer/archive/refs/heads/main.zip -o np.zip \
#     && python3 -c "import zipfile;zipfile.ZipFile('np.zip').extractall('.')" \
#     && bash nodepanel-installer-main/install.sh
set -e

PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  echo "未找到 python3 / python，请先安装 Python 3.8+"
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
"$PY" "$HERE/install.py" "$@"
