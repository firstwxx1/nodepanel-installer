# NodePanel · 一键安装版

自建代理节点管理面板（xray / sing-box）。两条命令装好，浏览器里管理全部节点。

## 一键安装

**Linux / macOS**

```bash
git clone https://github.com/firstwxx1/nodepanel-installer.git && cd nodepanel-installer && bash install.sh
```

不想用 git 就用 zip（不依赖 raw.githubusercontent）：

```bash
curl -fsSL https://github.com/firstwxx1/nodepanel-installer/archive/refs/heads/main.zip -o np.zip \
  && python3 -c "import zipfile;zipfile.ZipFile('np.zip').extractall('.')" \
  && python3 nodepanel-installer-main/install.py
```

**Windows**

下载仓库 zip 解压，双击 `install.bat`，或：

```powershell
python install.py
```

**已 clone 仓库** — 本地安装：

```bash
python install.py            # 装依赖 + 下载测速客户端 + 生成配置模板
python install.py --start    # 装完直接拉起面板
```

可选参数：

| 参数 | 作用 |
|---|---|
| `--dir PATH` | 指定安装目录（默认 `./nodepanel`）|
| `--start` | 安装后启动面板 |
| `--no-binaries` | 跳过 xray / hysteria 客户端下载 |
| `--no-deps` | 跳过 pip 依赖安装 |
| `--force` | 覆盖更新已有代码（保留 `data/nodes.json`）|

安装脚本会做四件事：部署源码 → `pip install paramiko segno cryptography` → 按平台下载 xray / hysteria 客户端（测速用）→ 从模板生成 `data/nodes.json`。

## 配置

安装后编辑 `data/nodes.json`（内容**不会**进 git）：

```bash
cp data/nodes.example.json data/nodes.json   # 若未自动生成
```

填服务器 SSH 信息、节点端口与参数，然后：

```bash
python app.py          # Windows 双击 run.bat
```

打开 **http://127.0.0.1:8765**

## 功能

- **两级页面**：一级服务器概览（状态 / 负载 / 内存 / 协议构成 / 连接数），二级该机节点详情
- **节点卡片**：一键复制链接、二维码、Clash 片段、重命名
- **测速**：本地临时起客户端穿过节点拉文件，返回 MB/s
- **用户管理**：VLESS 入站 UUID 增删，直写服务端配置
- **密钥轮换**：生成 x25519 密钥对 + shortId 写入服务端
- **新建节点**：以现有入站为模板克隆，重置 UUID / 密钥 / 端口
- **状态采集**：多台并行 SSH，约 13 秒刷完全部指标与监听表

## 写操作安全网

改用户 / 换密钥 / 新建 / 删除节点统一走：

1. 复制配置备份 `<config>.panel.bak`
2. 写入新配置
3. `xray -test` / `sing-box check` 校验
4. `systemctl restart` 并确认 active
5. 任一步失败**自动回滚**

## 结构

```
install.py     跨平台安装脚本
install.sh     Linux/macOS 一行命令入口
install.bat    Windows 双击入口
app.py         后端：SSH 采集、配置读写、链接构造、测速、二维码
ui/            两级路由前端
data/nodes.json   节点与服务器数据（自建，已 gitignore）
```

## 注意

面板只监听 `127.0.0.1`，外部访问不到。`data/nodes.json` 含 SSH 密码、Reality 私钥、节点 UUID，已在 `.gitignore` 中排除，任何情况下都不会提交。
