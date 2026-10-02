#!/usr/bin/env python3
"""NodePanel — 本地节点管理面板后端。

零 Web 框架依赖：stdlib http.server + paramiko（SSH 管理）+ segno（二维码）。
所有写操作先在服务器侧备份配置，改完重启并回读校验。
"""
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent import futures
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("需要 paramiko： pip install paramiko", file=sys.stderr)
    raise SystemExit(1)

try:
    import segno
except ImportError:
    segno = None

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
except ImportError:
    serialization = None
    X25519PrivateKey = None

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data" / "nodes.json"
UI_DIR = BASE / "ui"
HOST = "127.0.0.1"
PORT = 8765

LOCK = threading.Lock()
STATE = {"servers": [], "nodes": [], "local": {}, "status": None}


# ---------------------------------------------------------------- data layer

def load_data():
    with LOCK:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        STATE["servers"] = raw.get("servers", [])
        STATE["nodes"] = raw.get("nodes", [])
        STATE["local"] = raw.get("local", {})
        return raw


def save_data():
    with LOCK:
        payload = {
            "version": 1,
            "local": STATE["local"],
            "servers": STATE["servers"],
            "nodes": STATE["nodes"],
        }
        DATA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def get_server(sid):
    for s in STATE["servers"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"unknown server {sid}")


def get_node(nid):
    for n in STATE["nodes"]:
        if n["id"] == nid:
            return n
    raise KeyError(f"unknown node {nid}")


def node_server(node):
    return get_server(node["server_id"])


# ------------------------------------------------------------------- ssh ops

def ssh_exec(server, cmd, timeout=60):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        server["host"],
        username=server.get("ssh_user", "root"),
        password=server.get("ssh_pass", ""),
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return {"out": out, "err": err, "code": stdout.channel.recv_exit_status()}
    finally:
        client.close()


def remote_py(server, body, timeout=120):
    return ssh_exec(server, f"python3 - <<'PYEOF'\n{body}\nPYEOF", timeout=timeout)


def is_singbox(server):
    return server.get("engine") == "singbox"


def managed_service(node):
    server = node_server(node)
    return node.get("manage", {}).get("service", server.get("service"))


def managed_config(node):
    server = node_server(node)
    return node.get("manage", {}).get("config_path", server.get("config_path"))


# ------------------------------------------------------------ crypto helpers

def x25519_keypair():
    """本地生成 Reality 密钥对。返回 (priv_b64, pub_b64)，均为 urlsafe 无填充。"""
    if X25519PrivateKey is not None:
        priv = X25519PrivateKey.generate()
        priv_raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return b64u(priv_raw), b64u(pub_raw)
    # 退化：用 core 二进制在远端生成（xray / sing-box 都支持 x25519）
    raise RuntimeError("需要 cryptography： pip install cryptography（或用服务器 core 生成）")


def b64u(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_uuid():
    return str(uuid.uuid4())


# -------------------------------------------------------------- link builder

def build_link(node):
    svr = node_server(node)
    p = node.get("params", {})
    host = node.get("host") or svr["host"]
    port = node.get("port")
    label = urllib.parse.quote(node.get("label", node["id"]))
    proto = node["protocol"]

    if proto == "vless":
        q = {
            "encryption": "none",
            "security": "reality",
            "sni": p.get("sni", ""),
            "fp": p.get("fp", "chrome"),
            "pbk": p.get("pbk", ""),
            "sid": p.get("sid", ""),
            "type": "tcp",
        }
        if p.get("flow"):
            q["flow"] = p["flow"]
        return "vless://{}@{}:{}?{}#{}".format(
            p.get("uuid", ""), host, port, urllib.parse.urlencode(q), label
        )

    if proto == "hysteria2":
        q = {}
        if p.get("sni"):
            q["sni"] = p["sni"]
        if p.get("insecure"):
            q["insecure"] = "1"
        q["obfs"] = p.get("obfs", "")
        if p.get("obfs_password"):
            q["obfs-password"] = p["obfs_password"]
        if p.get("mport"):
            q["mport"] = p["mport"]
        q = {k: v for k, v in q.items() if v not in ("", None)}
        return "hy2://{}@{}:{}/?{}#{}".format(
            p.get("auth", ""), host, port, urllib.parse.urlencode(q), label
        )

    if proto == "socks5":
        cred = ""
        if p.get("username"):
            cred = "{}:{}@".format(p["username"], p["password"])
        return "socks5://{}{}:{}#{}".format(cred, host, port, label)

    return ""


def build_clash(node):
    svr = node_server(node)
    p = node.get("params", {})
    host = node.get("host") or svr["host"]
    proto = node["protocol"]
    if proto == "vless":
        return {
            "name": node.get("label"),
            "type": "vless",
            "server": host,
            "port": node.get("port"),
            "uuid": p.get("uuid"),
            "tls": True,
            "udp": True,
            "flow": p.get("flow", ""),
            "servername": p.get("sni"),
            "reality-opts": {"public-key": p.get("pbk"), "short-id": p.get("sid")},
            "client-fingerprint": p.get("fp", "chrome"),
            "network": "tcp",
        }
    if proto == "hysteria2":
        return {
            "name": node.get("label"),
            "type": "hysteria2",
            "server": host,
            "port": node.get("port"),
            "password": p.get("auth"),
            "skip-cert-verify": True,
            "obfs": p.get("obfs", ""),
            "obfs-password": p.get("obfs_password", ""),
        }
    if proto == "socks5":
        return {
            "name": node.get("label"),
            "type": "socks5",
            "server": host,
            "port": node.get("port"),
            "username": p.get("username", ""),
            "password": p.get("password", ""),
        }
    return {}


# ------------------------------------------------------- remote config patch

PATCH_ADD_USER = '''
import json, sys, shutil, time
path = {path!r}
tag = {tag!r}
newid = {newid!r}
label = {label!r}
flow = {flow!r}

shutil.copyfile(path, path + ".panel.bak")
c = json.load(open(path))
found = None
for ib in c["inbounds"]:
    tag_value = ib.get("tag")
    if tag_value == tag:
        found = ib
        break
if found is None:
    print(json.dumps({{"ok": False, "err": "inbound %s not found" % tag}}))
    sys.exit(0)

if "type" in found:  # sing-box
    entry = {{"uuid": newid, "name": label}}
    if flow:
        entry["flow"] = flow
    found.setdefault("users", []).append(entry)
else:  # xray
    entry = {{"id": newid, "email": label}}
    if flow:
        entry["flow"] = flow
    found.setdefault("settings", {{}}).setdefault("clients", []).append(entry)

json.dump(c, open(path, "w"), indent=2, ensure_ascii=False)
print(json.dumps({{"ok": True, "id": newid}}))
'''

PATCH_DEL_USER = '''
import json, sys, shutil
path = {path!r}
tag = {tag!r}
target = {target!r}

shutil.copyfile(path, path + ".panel.bak")
c = json.load(open(path))
found = None
for ib in c["inbounds"]:
    if ib.get("tag") == tag:
        found = ib
        break
if found is None:
    print(json.dumps({{"ok": False, "err": "inbound not found"}}))
    sys.exit(0)

removed = 0
if "type" in found:
    users = found.get("users", [])
    before = len(users)
    found["users"] = [u for u in users if (u.get("uuid") or u.get("password")) != target]
    removed = before - len(found["users"])
else:
    clients = found.get("settings", {{}}).get("clients", [])
    before = len(clients)
    found["settings"]["clients"] = [x for x in clients if x.get("id") != target]
    removed = before - len(found["settings"]["clients"])

json.dump(c, open(path, "w"), indent=2, ensure_ascii=False)
print(json.dumps({{"ok": True, "removed": removed}}))
'''

PATCH_LIST_USERS = '''
import json
path = {path!r}
try:
    c = json.load(open(path))
except Exception as e:
    print(json.dumps({{"ok": False, "err": str(e)}}))
    raise SystemExit(0)
out = []
for ib in c.get("inbounds", []):
    if ib.get("tag") != {tag!r}:
        continue
    if "type" in ib:
        for u in ib.get("users", []):
            out.append({{"id": u.get("uuid") or u.get("password") or u.get("name"),
                         "label": u.get("name", ""),
                         "flow": u.get("flow", "")}})
    else:
        for x in ib.get("settings", {{}}).get("clients", []):
            out.append({{"id": x.get("id"), "label": x.get("email", ""), "flow": x.get("flow", "")}})
print(json.dumps({{"ok": True, "users": out}}))
'''

PATCH_ROTATE_KEYS = '''
import json, shutil
path = {path!r}
tag = {tag!r}
priv = {priv!r}
sid = {sid!r}

shutil.copyfile(path, path + ".panel.bak")
c = json.load(open(path))
target = None
for ib in c["inbounds"]:
    if ib.get("tag") == tag:
        target = ib
        break
if target is None:
    print(json.dumps({{"ok": False, "err": "inbound not found"}}))
    raise SystemExit(0)

def sub(obj):
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            kl = k.lower()
            if kl == "privatekey" or kl == "private_key":
                obj[k] = priv
            elif kl in ("shortids", "shortid", "short_id"):
                obj[k] = [sid]
            else:
                sub(obj[k])
    elif isinstance(obj, list):
        for i in obj:
            sub(i)

sub(target)
json.dump(c, open(path, "w"), indent=2, ensure_ascii=False)
print(json.dumps({{"ok": True}}))
'''

PATCH_ADD_INBOUND = '''
import json, shutil, sys
path = {path!r}
template_tag = {template!r}
new_tag = {new_tag!r}
new_port = {new_port!r}
new_id = {new_id!r}
priv = {priv!r}
sid = {sid!r}
label = {label!r}
sni = {sni!r}

shutil.copyfile(path, path + ".panel.bak")
c = json.load(open(path))
tpl = None
for ib in c["inbounds"]:
    if ib.get("tag") == template_tag:
        tpl = json.loads(json.dumps(ib))
        break
if tpl is None:
    print(json.dumps({{"ok": False, "err": "template inbound not found"}}))
    sys.exit(0)

tpl["tag"] = new_tag
tpl["listen"] = "0.0.0.0"
if "type" in tpl:  # sing-box：只认 listen_port，且不接受 port 字段
    tpl["listen_port"] = new_port
    tpl.pop("port", None)
else:
    tpl["port"] = new_port

if "type" in tpl:  # sing-box
    users = [{{"uuid": new_id, "name": label, "flow": tpl.get("users", [{{}}])[0].get("flow", "xtls-rprx-vision")}}]
    tpl["users"] = users
    def deep(o):
        if isinstance(o, dict):
            for k in list(o.keys()):
                kl = k.lower()
                if kl == "private_key":
                    o[k] = priv
                elif kl == "short_id":
                    o[k] = [sid]
                elif sni and kl in ("server_name", "servername"):
                    o[k] = sni
                elif sni and kl == "servernames":
                    o[k] = [sni]
                else:
                    deep(o[k])
        elif isinstance(o, list):
            for i in o:
                deep(i)
    deep(tpl)
else:  # xray
    flow = "xtls-rprx-vision"
    try:
        flow = tpl.get("settings", {{}}).get("clients", [{{}}])[0].get("flow") or flow
    except Exception:
        pass
    tpl["settings"] = tpl.get("settings", {{}})
    tpl["settings"]["clients"] = [{{"id": new_id, "flow": flow, "email": label}}]
    tpl["settings"]["decryption"] = "none"
    rs = tpl.get("streamSettings", {{}}).get("realitySettings", {{}})
    if rs:
        rs["privateKey"] = priv
        rs["shortIds"] = [sid]
        if sni:
            rs["serverNames"] = [sni]

c["inbounds"].append(tpl)
json.dump(c, open(path, "w"), indent=2, ensure_ascii=False)
print(json.dumps({{"ok": True, "tag": new_tag, "port": new_port}}))
'''

PATCH_DEL_INBOUND = '''
import json, shutil
path = {path!r}
tag = {tag!r}

shutil.copyfile(path, path + ".panel.bak")
c = json.load(open(path))
before = len(c.get("inbounds", []))
c["inbounds"] = [ib for ib in c.get("inbounds", []) if ib.get("tag") != tag]
json.dump(c, open(path, "w"), indent=2, ensure_ascii=False)
print(json.dumps({{"ok": True, "removed": before - len(c["inbounds"])}}))
'''

STATUS_SCRIPT = r'''
import json, os, re, subprocess, sys

services = json.loads(sys.argv[1])
core = sys.argv[2]

def sh(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return "ERR:%s" % e

info = {}
info["hostname"] = sh("hostname").strip()
info["uptime"] = sh("uptime -p").strip()
load = sh("cat /proc/loadavg").split()
info["load"] = float(load[0]) if load else -1
info["cpu"] = os.cpu_count()

mu = sh("free -m").splitlines()
if len(mu) > 1:
    parts = mu[1].split()
    if len(parts) >= 3:
        total = int(parts[1])
        used = int(parts[2])
        info["mem"] = {"total": total, "used": used, "pct": round(used * 100.0 / total, 1)}
df = sh("df -h /").splitlines()
if len(df) > 1:
    parts = df[1].split()
    if len(parts) >= 5:
        info["disk"] = {"size": parts[1], "used": parts[2], "pct": parts[4]}

# 监听端口
listening = set()
for line in sh("ss -tuln").splitlines():
    m = re.search(r":(\d+)\s", line)
    if m:
        listening.add(int(m.group(1)))
info["listening"] = sorted(listening)

# 每端口已建立连接数
conns = {}
for line in sh("ss -tn").splitlines():
    parts = line.split()
    for mark in ("ESTAB", "ESTABLISHED"):
        if mark in line and len(parts) >= 5:
            def port_of(addr):
                return int(addr.rsplit(":", 1)[1]) if ":" in addr else -1
            local = port_of(parts[3])
            if local > 0:
                conns[local] = conns.get(local, 0) + 1
            break
info["conns"] = conns

# 服务状态 + core 版本
svc = {}
for name in services:
    svc[name] = sh("systemctl is-active %s" % name).strip()
info["services"] = svc
ver = ""
if core:
    try:
        ver = subprocess.run([core, "version"], capture_output=True, text=True, timeout=10)
        ver = (ver.stdout or ver.stderr or "").strip().splitlines()
        ver = ver[0] if ver else ""
    except Exception:
        ver = ""
info["version"] = ver
print("@@JSON@@" + json.dumps(info))
'''


def parse_json_block(text, marker="@@JSON@@"):
    idx = text.find(marker)
    if idx < 0:
        raise RuntimeError("remote script returned no payload:\n" + text[-2000:])
    return json.loads(text[idx + len(marker):].strip().splitlines()[0])


# ----------------------------------------------------------------- api impl

def restart_and_verify(server, service):
    ssh_exec(server, f"systemctl daemon-reload; systemctl restart {service}", timeout=60)
    time.sleep(2)
    r = ssh_exec(server, f"systemctl is-active {service}")
    return r["out"].strip() == "active"


def server_services(svr):
    names = {svr.get("service")}
    for n in STATE["nodes"]:
        if n["server_id"] == svr["id"]:
            m = n.get("manage", {})
            if m.get("service"):
                names.add(m["service"])
    return sorted(x for x in names if x)


def probe_server(svr):
    services = server_services(svr)
    arg = json.dumps(services)
    cmd = f"python3 - {arg!r} {svr.get('core_bin','')!r} <<'PYEOF'\n{STATUS_SCRIPT}\nPYEOF"
    r = ssh_exec(svr, cmd, timeout=60)
    return parse_json_block(r["out"])


def api_status_refresh():
    result = {"ts": datetime.now().strftime("%H:%M:%S"), "servers": {}, "errors": {}}
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(probe_server, svr): svr["id"] for svr in STATE["servers"]}
        for job in futures.as_completed(jobs):
            sid = jobs[job]
            try:
                result["servers"][sid] = job.result()
            except Exception as e:
                result["errors"][sid] = str(e)
    STATE["status"] = result
    return result


def tcp_check(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def api_speedtest(node):
    """本地起代理客户端 → curl 下载 → 返回 MB/s。"""
    cfg = STATE["local"]
    url = cfg.get("speedtest_url", "https://proof.ovh.net/files/10Mb.dat")
    tmo = int(cfg.get("speedtest_timeout", 45))
    svr = node_server(node)
    p = node.get("params", {})
    host = node.get("host") or svr["host"]
    port = int(node.get("port"))
    bin_xray = str((BASE / cfg.get("client_xray", "../xray.exe")).resolve())
    bin_hy = str((BASE / cfg.get("client_hysteria", "../hysteria.exe")).resolve())
    proto = node["protocol"]
    tmpdir = tempfile.mkdtemp(prefix="nodepanel-")
    proc = None
    socks_port = free_port()

    try:
        if proto == "vless":
            if not os.path.exists(bin_xray):
                return {"ok": False, "err": f"缺本地 xray 客户端：{bin_xray}"}
            cfgfile = os.path.join(tmpdir, "client.json")
            Path(cfgfile).write_text(json.dumps({
                "log": {"loglevel": "error"},
                "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks",
                              "settings": {"udp": True}}],
                "outbounds": [{
                    "protocol": "vless",
                    "settings": {"vnext": [{"address": host, "port": port,
                                            "users": [{"id": p.get("uuid"), "encryption": "none",
                                                       **({"flow": p["flow"]} if p.get("flow") else {})}]}]},
                    "streamSettings": {
                        "network": "raw",
                        "security": "reality",
                        "realitySettings": {"serverName": p.get("sni"), "fingerprint": p.get("fp", "chrome"),
                                            "publicKey": p.get("pbk"), "shortId": p.get("sid")},
                    },
                }],
            }, indent=2), encoding="utf-8")
            proc = spawn([bin_xray, "run", "-c", cfgfile])

        elif proto == "hysteria2":
            if not os.path.exists(bin_hy):
                return {"ok": False, "err": f"缺本地 hysteria 客户端：{bin_hy}"}
            cfgfile = os.path.join(tmpdir, "client.yaml")
            lines = [f"server: {host}:{port}",
                     f"auth: {p.get('auth')}",
                     "tls:", "  insecure: true"]
            if p.get("sni"):
                lines.append(f"  sni: {p.get('sni')}")
            if p.get("obfs"):
                lines += ["obfs:", f"  type: {p.get('obfs')}", "  salamander:",
                          f"    password: {p.get('obfs_password')}"]
            lines += ["socks5:", f"  listen: 127.0.0.1:{socks_port}"]
            Path(cfgfile).write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = spawn([bin_hy, "client", "-c", cfgfile])

        elif proto == "socks5":
            cred = ""
            if p.get("username"):
                cred = f"{p['username']}:{p['password']}@"
            r = curl_run(f"{cred}{host}:{port}", url, tmo)
            return parse_curl(r, extra={"mode": "direct"})

        else:
            return {"ok": False, "err": f"不支持的协议 {proto}"}

        time.sleep(2.5)
        if proc.poll() is not None:
            tail = ""
            try:
                tail = (proc.stdout.read() or b"").decode("utf-8", "replace")[-400:]
            except Exception:
                pass
            return {"ok": False, "err": "本地客户端启动失败 " + tail}

        r = curl_run(f"127.0.0.1:{socks_port}", url, tmo)
        return parse_curl(r, extra={"mode": "proxy", "socks_port": socks_port})
    finally:
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def spawn(args):
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)


def curl_run(socks_spec, url, timeout):
    """socks_spec 形如 127.0.0.1:1080 或 user:pass@host:port。
    必须走参数数组：Windows cmd 会把 %{...} 当环境变量吞掉。"""
    args = ["curl", "-sL", "-o", os.devnull, "--max-time", str(timeout),
            "--socks5", socks_spec, "-w", "%{speed_download}|%{size_download}|%{time_total}|%{http_code}", url]
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 10,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return (p.stdout or "").strip()


def parse_curl(raw, extra=None):
    parts = (raw or "").split("|")
    res = {"ok": False, "raw": raw}
    if len(parts) >= 4:
        try:
            speed = float(parts[0])
            size = int(parts[1])
            took = float(parts[2])
            code = parts[3]
            res = {
                "ok": size > 0,
                "speed": round(speed / 1048576, 2),
                "mbps": round(speed * 8 / 1000000, 1),
                "size_mb": round(size / 1048576, 2),
                "seconds": round(took, 2),
                "http": code,
            }
        except ValueError:
            pass
    if extra:
        res.update(extra)
    return res


def api_users(node_id):
    node = get_node(node_id)
    if node["protocol"] not in ("vless",):
        return {"ok": False, "err": "该协议不支持用户管理"}
    svr = node_server(node)
    script = PATCH_LIST_USERS.format(path=managed_config(node), tag=node.get("inbound_tag"))
    r = remote_py(svr, script)
    try:
        return json.loads(r["out"].strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "err": r["out"][-400:] or r["err"][-400:]}


def run_patch(node, script):
    """执行远端补丁脚本 → 重启服务 → 回读新配置，失败自动回滚备份。"""
    svr = node_server(node)
    cfg = managed_config(node)
    svc = managed_service(node)
    r = remote_py(svr, script)
    payload = None
    try:
        payload = json.loads(r["out"].strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "err": "脚本输出异常: " + (r["out"] or r["err"])[-400:]}
    if not payload.get("ok"):
        return payload

    check_cmd = f"{svr.get('core_bin')} check -c {cfg}" if svr.get("engine") == "singbox" else \
                f"{svr.get('core_bin')} -test -config {cfg}"
    ver = ssh_exec(svr, check_cmd, timeout=30)
    if ver["code"] != 0:
        ssh_exec(svr, f"cp {cfg}.panel.bak {cfg}")
        return {"ok": False, "err": "配置校验失败，已回滚", "detail": ver["err"][-300:] or ver["out"][-300:]}

    if not restart_and_verify(svr, svc):
        ssh_exec(svr, f"cp {cfg}.panel.bak {cfg} && systemctl restart {svc}")
        return {"ok": False, "err": "重启后服务未 active，已回滚"}

    payload["verified"] = True
    return payload


def api_add_user(node_id, label):
    node = get_node(node_id)
    uid = new_uuid()
    script = PATCH_ADD_USER.format(
        path=managed_config(node), tag=node.get("inbound_tag"),
        newid=uid, label=label or "user-" + uid[:8], flow=node.get("params", {}).get("flow", "")
    )
    res = run_patch(node, script)
    if res.get("ok"):
        sub = json.loads(json.dumps(node))
        sub["params"] = dict(node.get("params", {}))
        sub["params"]["uuid"] = uid
        res["link"] = build_link(sub)
        res["uuid"] = uid
    return res


def api_del_user(node_id, target):
    node = get_node(node_id)
    script = PATCH_DEL_USER.format(path=managed_config(node), tag=node.get("inbound_tag"), target=target)
    return run_patch(node, script)


def api_rotate(node_id):
    node = get_node(node_id)
    if node["protocol"] != "vless":
        return {"ok": False, "err": "只有 VLESS 支持密钥轮换"}
    priv, pub = x25519_keypair()
    sid = secrets.token_hex(8)
    script = PATCH_ROTATE_KEYS.format(path=managed_config(node), tag=node.get("inbound_tag"),
                                      priv=priv, sid=sid)
    res = run_patch(node, script)
    if res.get("ok"):
        node["params"]["pbk"] = pub
        node["params"]["sid"] = sid
        save_data()
        res["pbk"] = pub
        res["sid"] = sid
        res["link"] = build_link(node)
    return res


def api_new_node(server_id, port, label, template_protocol="vless", sni=None):
    svr = get_server(server_id)
    allowed = svr.get("allowed_ports", [])
    if port not in allowed:
        return {"ok": False, "err": f"端口 {port} 不在该服务器放行列表 {allowed} 内（安全组/防火墙）"}
    taken = {int(n["port"]) for n in STATE["nodes"] if n["server_id"] == server_id}
    if port in taken:
        return {"ok": False, "err": f"端口 {port} 已被该服务器上的节点占用"}

    tpl_node = None
    for n in STATE["nodes"]:
        if n["server_id"] == server_id and n["protocol"] == template_protocol:
            tpl_node = n
            break
    if not tpl_node:
        return {"ok": False, "err": "该服务器上没有同协议模板入站"}
    template_tag = tpl_node.get("inbound_tag")
    if not template_tag:
        return {"ok": False, "err": "模板节点缺少 inbound_tag"}

    new_tag = f"vless-{port}"
    uid = new_uuid()
    priv, pub = x25519_keypair()
    sid = secrets.token_hex(8)
    script = PATCH_ADD_INBOUND.format(
        path=svr["config_path"], template=template_tag, new_tag=new_tag,
        new_port=port, new_id=uid, priv=priv, sid=sid, label=label or new_tag, sni=sni or "",
    )
    res = run_patch({"server_id": server_id, "manage": {}}, script)
    if not res.get("ok"):
        return res
    time.sleep(1)
    check = ssh_exec(svr, f"ss -tlnp | grep -c ':{port} '")
    if check["out"].strip() in ("0", ""):
        return {"ok": False, "err": f"配置已写入但 {port} 未监听（可能被安全组或已有进程占用）"}

    tpl_params = json.loads(json.dumps(tpl_node.get("params", {})))
    tpl_params.update({"uuid": uid, "pbk": pub, "sid": sid})
    if sni:
        tpl_params["sni"] = sni
    node = {
        "id": f"{server_id}-{new_tag}",
        "server_id": server_id,
        "label": label or f"{svr['id'].upper()}-{new_tag}",
        "protocol": "vless",
        "port": port,
        "inbound_tag": new_tag,
        "params": tpl_params,
    }
    STATE["nodes"].append(node)
    save_data()
    return {"ok": True, "node": node, "link": build_link(node)}


def api_del_node(node_id):
    node = get_node(node_id)
    svr = node_server(node)
    tag = node.get("inbound_tag")
    cfg = managed_config(node)
    if not tag:
        return {"ok": False, "err": "该节点无 inbound_tag，无法定位入站"}
    count = sum(1 for n in STATE["nodes"] if n.get("inbound_tag") == tag and n["server_id"] == node["server_id"])
    if count > 1:
        return {"ok": False, "err": f"入站 {tag} 被 {count} 个节点共用，拒绝删除"}
    script = PATCH_DEL_INBOUND.format(path=cfg, tag=tag)
    res = run_patch(node, script)
    if res.get("ok"):
        STATE["nodes"] = [n for n in STATE["nodes"] if n["id"] != node_id]
        save_data()
    return res


# ---------------------------------------------------------------- http layer

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI_DIR), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _safe(self, fn, *args):
        try:
            return self._json(fn(*args))
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "err": f"{type(e).__name__}: {e}"}, 500)

    # -- routes
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/servers":
            return self._json([{k: v for k, v in s.items() if k != "ssh_pass"} for s in STATE["servers"]])
        if path == "/api/nodes":
            out = []
            for n in STATE["nodes"]:
                svr = node_server(n)
                item = dict(n)
                item["server_name"] = svr.get("name")
                item["host"] = n.get("host") or svr["host"]
                item["link"] = build_link(n)
                item["clash"] = build_clash(n)
                out.append(item)
            return self._json({"nodes": out, "status": STATE.get("status")})
        if path == "/api/status":
            return self._json(STATE.get("status") or {"servers": {}, "errors": {}})
        if path == "/api/qr":
            return self._qr(qs.get("text", [""])[0])
        if path == "/api/ping":
            return self._json({"ok": True, "time": time.time()})

        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()

        if path == "/api/status/refresh":
            return self._safe(api_status_refresh)
        if path == "/api/reload":
            load_data()
            return self._json({"ok": True, "nodes": len(STATE["nodes"])})
        if path == "/api/reach":
            def check():
                node = get_node(body["id"])
                svr = node_server(node)
                host = node.get("host") or svr["host"]
                port = int(node.get("port"))
                if node["protocol"] == "hysteria2":
                    return {"ok": True, "udp": True, "note": "UDP 端口无法用 TCP 握手探测"}
                t0 = time.time()
                ok = tcp_check(host, port)
                return {"ok": ok, "ms": round((time.time() - t0) * 1000, 1)}
            return self._safe(check)
        if path == "/api/users":
            return self._safe(api_users, body["id"])
        if path == "/api/users/add":
            return self._safe(api_add_user, body["id"], body.get("label", ""))
        if path == "/api/users/del":
            return self._safe(api_del_user, body["id"], body["uuid"])
        if path == "/api/rotate":
            return self._safe(api_rotate, body["id"])
        if path == "/api/speedtest":
            return self._safe(api_speedtest, get_node(body["id"]))
        if path == "/api/node/new":
            return self._safe(api_new_node, body["server_id"], int(body["port"]),
                              body.get("label", ""), body.get("protocol", "vless"), body.get("sni"))
        if path == "/api/node/del":
            return self._safe(api_del_node, body["id"])
        if path == "/api/node/update":
            def upd():
                node = get_node(body["id"])
                if "label" in body:
                    node["label"] = body["label"]
                if "params" in body:
                    node["params"].update(body["params"])
                save_data()
                return {"ok": True, "link": build_link(node)}
            return self._safe(upd)

        return self._json({"ok": False, "err": "no route"}, 404)

    def _qr(self, text):
        if not segno:
            return self._json({"ok": False, "err": "缺 segno： pip install segno"}, 501)
        import io
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=7, border=2)
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    load_data()
    print(f"NodePanel 已启动： http://{HOST}:{PORT}")
    print(f"数据文件： {DATA_FILE}")
    print(f"节点数： {len(STATE['nodes'])}  服务器： {len(STATE['servers'])}")
    if not segno:
        print("提示：缺 segno，二维码功能不可用（pip install segno）")
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
