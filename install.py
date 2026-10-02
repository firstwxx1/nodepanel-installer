#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NodePanel 一键安装脚本

用法：
    python install.py                  # 安装到 ./nodepanel 并准备就绪
    python install.py --dir ~/np       # 指定目录
    python install.py --start          # 装完直接启动面板
    python install.py --no-binaries    # 不下载 xray / hysteria（测速功能会缺失）
    python install.py --force          # 目标目录已存在也覆盖更新代码

已经 clone 了仓库时，在本目录直接跑即可，脚本会跳过下载源码这一步。
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

REPO = "firstwxx1/nodepanel-installer"
BRANCH = "main"
ZIP_URL = "https://github.com/%s/archive/refs/heads/%s.zip" % (REPO, BRANCH)
RAW_INSTALL = "https://raw.githubusercontent.com/%s/%s/install.py" % (REPO, BRANCH)

DEPS = ["paramiko", "segno", "cryptography"]
PORT = 8765

GH_XRAY = "https://github.com/XTLS/Xray-core/releases/latest/download/"
GH_HY = "https://github.com/apernet/hysteria/releases/latest/download/"


def log(msg):
    print(msg, flush=True)


def ok(msg):
    log("  [ok] " + msg)


def warn(msg):
    log("  [!!] " + msg)


def download(url, dest, timeout=120):
    log("  下载 %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "nodepanel-installer"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def pip_install_deps():
    missing = []
    for m in DEPS:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if not missing:
        ok("依赖已齐全（%s）" % ", ".join(DEPS))
        return []
    log("  安装依赖：%s" % ", ".join(missing))
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + missing
    subprocess.run(cmd, check=False)
    still = []
    for m in DEPS:
        try:
            __import__(m)
        except ImportError:
            still.append(m)
    if still:
        warn("以下依赖安装失败，面板部分功能不可用：%s" % ", ".join(still))
    else:
        ok("依赖安装完成")
    return still


def fetch_source(dest, force):
    """下载仓库源码到 dest；仓库内已含源码时直接使用。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "app.py")) and os.path.isdir(os.path.join(here, "ui")):
        ok("已在仓库目录内，使用当前源码")
        return here

    if os.path.isfile(os.path.join(dest, "app.py")) and not force:
        ok("目标目录已存在面板代码，跳过下载（加 --force 强制更新）")
        return dest

    tmp_zip = os.path.join(tempfile.gettempdir(), "nodepanel-src.zip")
    try:
        download(ZIP_URL, tmp_zip)
    except Exception as e:
        warn("源码下载失败：%s" % e)
        if os.path.isfile(os.path.join(dest, "app.py")):
            return dest
        raise SystemExit("无法获取源码，请检查网络或改用 git clone 后本地安装")

    tmp_dir = tempfile.mkdtemp(prefix="nodepanel-")
    with zipfile.ZipFile(tmp_zip) as z:
        z.extractall(tmp_dir)
    root = os.path.join(tmp_dir, os.listdir(tmp_dir)[0])
    os.makedirs(dest, exist_ok=True)
    for item in os.listdir(root):
        s = os.path.join(root, item)
        d = os.path.join(dest, item)
        if os.path.isdir(s):
            if os.path.isdir(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.remove(tmp_zip)
    ok("源码已部署到 %s" % dest)
    return dest


def bin_spec():
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    if sysname == "windows":
        return [
            ("xray.exe", GH_XRAY + "Xray-windows-64.zip", "zip", "xray.exe"),
            ("hysteria.exe", GH_HY + "hysteria-windows-amd64.exe", "raw", None),
        ]
    if sysname == "darwin":
        arch = "arm64" if "arm" in machine else "64"
        return [
            ("xray", GH_XRAY + "Xray-macos-%s.zip" % arch, "zip", "xray"),
            ("hysteria", GH_HY + "hysteria-darwin-amd64", "raw", None),
        ]
    arch = "arm64-v8a" if "aarch64" in machine or "arm" in machine else "64"
    return [
        ("xray", GH_XRAY + "Xray-linux-%s.zip" % arch, "zip", "xray"),
        ("hysteria", GH_HY + "hysteria-linux-amd64", "raw", None),
    ]


def install_binaries(dest):
    for name, url, kind, inner in bin_spec():
        target = os.path.join(dest, name)
        if os.path.isfile(target):
            ok("%s 已存在，跳过" % name)
            continue
        try:
            if kind == "raw":
                download(url, target)
            else:
                tmp = target + ".zip"
                download(url, tmp)
                with zipfile.ZipFile(tmp) as z:
                    with z.open(inner) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                os.remove(tmp)
            if platform.system().lower() != "windows":
                os.chmod(target, 0o755)
            ok("%s 就绪" % name)
        except Exception as e:
            warn("%s 下载失败（测速功能将不可用，可稍后手动放置）：%s" % (name, e))


def prepare_config(dest):
    data_dir = os.path.join(dest, "data")
    os.makedirs(data_dir, exist_ok=True)
    real = os.path.join(data_dir, "nodes.json")
    example = os.path.join(data_dir, "nodes.example.json")
    if os.path.isfile(real):
        ok("已存在 data/nodes.json，保留现有配置")
        return True
    if os.path.isfile(example):
        shutil.copy2(example, real)
        warn("已生成 data/nodes.json 模板——请填入你的服务器与节点信息后再使用")
        return False
    warn("未找到模板文件，需自行创建 data/nodes.json")
    return False


def start_panel(dest):
    log("  启动面板：http://127.0.0.1:%d" % PORT)
    try:
        subprocess.Popen([sys.executable, "app.py"], cwd=dest,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok("已后台启动，浏览器打开 http://127.0.0.1:%d" % PORT)
    except Exception as e:
        warn("启动失败：%s" % e)


def main():
    ap = argparse.ArgumentParser(description="NodePanel 一键安装")
    ap.add_argument("--dir", default=None, help="安装目录，默认 ./nodepanel")
    ap.add_argument("--start", action="store_true", help="安装完成后启动面板")
    ap.add_argument("--no-binaries", action="store_true", help="不下载 xray / hysteria 客户端")
    ap.add_argument("--no-deps", action="store_true", help="跳过 pip 依赖安装")
    ap.add_argument("--force", action="store_true", help="强制覆盖更新已有代码")
    args = ap.parse_args()

    log("")
    log("NodePanel 一键安装")
    log("=" * 46)

    here = os.path.dirname(os.path.abspath(__file__))
    in_repo = os.path.isfile(os.path.join(here, "app.py"))
    dest = os.path.abspath(args.dir) if args.dir else (here if in_repo else os.path.join(os.getcwd(), "nodepanel"))

    log("[1/4] 获取源码")
    dest = fetch_source(dest, args.force)

    log("[2/4] 安装 Python 依赖")
    if args.no_deps:
        log("  跳过")
    else:
        pip_install_deps()

    log("[3/4] 下载本地测速客户端")
    if args.no_binaries:
        log("  跳过")
    else:
        install_binaries(dest)

    log("[4/4] 准备配置文件")
    ready = prepare_config(dest)

    log("")
    log("=" * 46)
    log("安装目录：%s" % dest)
    if ready:
        log("启动： python app.py      （Windows 双击 run.bat）")
        log("然后打开 http://127.0.0.1:%d" % PORT)
    else:
        log("下一步：编辑 %s 填入服务器与节点，然后" % os.path.join(dest, "data", "nodes.json"))
        log("        python app.py     （Windows 双击 run.bat）")
    log("")

    if args.start:
        start_panel(dest)


if __name__ == "__main__":
    main()
