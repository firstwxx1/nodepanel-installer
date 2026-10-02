/* NodePanel UI — 原生 JS。两级页面：一级=服务器概览，二级=该机节点详情。无弹层。 */
const $ = (s) => document.querySelector(s);
const TOAST = $("#toast");
let NODES = [], SERVERS = [], STATUS = null;
let EXP = null;      // 详情卡片展开：{ id, act }
let NEWSVR = null;   // 详情新建表单展开的服务器 id

/* ------------------------------------------------------------ 基础 */

function toast(msg, ms = 2600) {
  TOAST.textContent = msg;
  TOAST.hidden = false;
  clearTimeout(TOAST._t);
  TOAST._t = setTimeout(() => (TOAST.hidden = true), ms);
}

async function api(path, method = "GET", body) {
  const opt = { method };
  if (body) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  return await r.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const shortId = (s, n = 14) => (s == null ? "-" : s.length > n ? s.slice(0, n) + "…" : s);
const route = () => (location.hash.match(/^#\/s\/(.+)$/) || [])[1] || null;

function go(sid) { location.hash = sid ? "#/s/" + sid : "#/"; }

/* ------------------------------------------------------------ 公共片段 */

function servicesLine(st) {
  return Object.entries(st.services || {})
    .map(([name, state]) => `<span class="svc ${state === "active" ? "on" : "off"}">${esc(name)}</span>`)
    .join("");
}

function metricsHtml(st, full) {
  if (!st) return `<span class="dim">状态未采集</span>`;
  const m = [
    `<span class="metric">⚖ ${st.load}</span>`,
    `<span class="metric">◆ ${st.cpu} 核</span>`,
    `<span class="metric">▣ 内存 ${st.mem?.pct}%</span>`,
  ];
  if (full) {
    m.push(`<span class="metric">▤ 磁盘 ${st.disk?.pct}%</span>`, `<span class="metric">⏱ ${esc(st.uptime || "-")}</span>`);
    if (st.version) m.push(`<span class="metric ver">${esc(st.version)}</span>`);
  }
  return m.join("");
}

const protoCount = (nodes) => {
  const c = {};
  nodes.forEach((n) => (c[n.protocol] = (c[n.protocol] || 0) + 1));
  return Object.entries(c).map(([p, n]) => `<span class="tag ${p}">${p} ×${n}</span>`).join("");
};

/* ------------------------------------------------------------ 一级：服务器概览 */

function overviewHtml() {
  return `<div class="ov">` + SERVERS.map((s) => {
    const st = STATUS?.servers?.[s.id];
    const err = STATUS?.errors?.[s.id];
    const active = st?.services?.[s.service] === "active";
    const nodes = NODES.filter((n) => n.server_id === s.id);
    const busy = nodes.reduce((a, n) => a + (st?.conns?.[n.port] || 0), 0);
    return `
      <div class="srv-card" data-sid="${s.id}">
        <div class="sc-top">
          <span class="gh-dot ${err ? "bad" : active ? "ok" : ""}"></span>
          <span class="sc-name">${esc(s.name)}</span>
          <span class="pill ${err ? "bad" : active ? "ok" : ""}">${err ? "SSH 失败" : active ? "运行中" : "未采集"}</span>
        </div>
        <div class="sc-host"><code>${esc(s.host)}</code> · ${esc(s.engine)}</div>
        <div class="sc-tags">${protoCount(nodes) || `<span class="hint">无节点</span>`}</div>
        <div class="sc-metrics">${metricsHtml(st, false)}</div>
        <div class="sc-services">${st ? servicesLine(st) : ""}</div>
        <div class="sc-foot">
          <span class="dim">${nodes.length} 节点 · ${busy} 连接</span>
          <button class="sm primary" data-go="${s.id}">节点详情 →</button>
        </div>
      </div>`;
  }).join("") + `</div>`;
}

/* ------------------------------------------------------------ 二级：单台节点详情 */

function extraLink(n) {
  const clash = "proxies:\n" + yamlDump([n.clash]);
  return `
    <div class="qrbox">
      <img src="/api/qr?text=${encodeURIComponent(n.link)}" alt="qr">
      <div class="qrside">
        <div class="hint">分享链接</div>
        <textarea id="lnk_${n.id}" readonly>${esc(n.link)}</textarea>
        <div class="row">
          <button class="primary sm" onclick="copy('#lnk_${n.id}')">复制链接</button>
          <button class="sm" onclick="copy('#clash_${n.id}')">复制 Clash</button>
        </div>
      </div>
    </div>
    <div class="field"><label>Clash / sing-box 片段</label><textarea id="clash_${n.id}" readonly>${esc(clash)}</textarea></div>
    <div class="field"><label>备注名</label>
      <div class="row"><input id="lbl_${n.id}" value="${esc(n.label)}" class="grow"><button class="sm" onclick="rename('${n.id}')">保存</button></div>
    </div>`;
}

const extraUsers = (n) => `
  <div id="usersBody_${n.id}"><span class="spin"></span> 读取服务端配置…</div>
  <div class="field"><label>新增用户（自动生成 UUID）</label>
    <div class="row"><input id="newLabel_${n.id}" placeholder="备注名，可留空" class="grow"><button class="primary sm" onclick="addUser('${n.id}')">添加</button></div>
  </div>
  <div id="addResult_${n.id}"></div>`;

const extraSpeed = (n) => `
  <div class="hint">本地临时启动代理客户端，拉取测速文件后自动退出。</div>
  <div id="spdResult_${n.id}" class="result"><button class="primary sm" onclick="runSpeed('${n.id}')">开始测速</button></div>`;

const extraRotate = (n) => `
  <div class="hint">生成新的 x25519 密钥对和 shortId，写入服务端并重启，旧链接立即失效。</div>
  <div class="row"><button class="primary sm" onclick="doRotate('${n.id}')">确认轮换</button></div>
  <div id="rotOut_${n.id}"></div>`;

function extraHtml(n) {
  if (!EXP || EXP.id !== n.id) return "";
  return { link: extraLink, users: extraUsers, speed: extraSpeed, rotate: extraRotate }[EXP.act](n) || "";
}

function newFormHtml(s) {
  if (NEWSVR !== s.id) return "";
  return `
    <div class="card newform">
      <div class="card-top"><span class="card-name">在此机新建 VLESS 节点</span><span class="tag vless">new</span></div>
      <div class="hint">以现有 VLESS 入站为模板克隆，自动重置 UUID / Reality 密钥 / shortId。放行端口 ${esc(s.allowed_ports.join("/"))}</div>
      <div class="field"><label>端口（须在放行列表内）</label><input id="nPort_${s.id}" type="number" placeholder="8443"></div>
      <div class="field"><label>备注名</label><input id="nLabel_${s.id}" placeholder="可留空"></div>
      <div class="field"><label>伪装 SNI（留空沿用模板）</label><input id="nSni_${s.id}" placeholder="www.cloudflare.com"></div>
      <div class="row"><button class="primary sm" onclick="createNode('${s.id}')">创建</button><button class="sm" onclick="NEWSVR=null;render()">取消</button></div>
      <div id="newOut_${s.id}"></div>
    </div>`;
}

function nodeCard(n) {
  const st = STATUS?.servers?.[n.server_id];
  const conns = st?.conns?.[n.port];
  const listening = st?.listening?.includes(Number(n.port));
  const cred = n.params.uuid || n.params.auth || n.params.username || "-";
  const open = EXP && EXP.id === n.id;
  const act = open ? EXP.act : null;
  return `
  <div class="card${open ? " open" : ""}" data-id="${n.id}">
    <div class="card-top">
      <span class="card-name" title="${esc(n.label)}">${esc(n.label)}</span>
      <span class="tag ${n.protocol}">${n.protocol}</span>
    </div>
    <div class="card-addr"><code>${esc(n.host)}:${n.port}</code></div>
    <div class="card-info">
      <div class="kv"><span>UUID / 口令</span><b class="mono" title="${esc(cred)}">${esc(shortId(cred, 18))}</b></div>
      <div class="kv"><span>端口监听</span><b>${listening == null ? "?" : listening ? '<i class="ok-txt">正常</i>' : '<i class="bad-txt">否</i>'}</b></div>
      <div class="kv"><span>活动连接</span><b>${conns ?? "?"}</b></div>
    </div>
    ${n.note ? `<div class="hint">${esc(n.note)}</div>` : ""}
    <div class="card-btns">
      <button class="primary sm" data-act="copy">复制链接</button>
      <button class="sm${act === "link" ? " on" : ""}" data-act="link">二维码</button>
      <button class="sm${act === "speed" ? " on" : ""}" data-act="speed">测速</button>
      ${n.protocol === "vless" ? `<button class="sm${act === "users" ? " on" : ""}" data-act="users">用户</button>
      <button class="sm${act === "rotate" ? " on" : ""}" data-act="rotate">换密钥</button>` : ""}
      <button class="sm" data-act="reach">连通</button>
      <button class="sm danger" data-act="del" title="删除">✕</button>
    </div>
    ${open ? `<div class="extra">${extraHtml(n)}</div>` : ""}
  </div>`;
}

function detailHtml(s) {
  const st = STATUS?.servers?.[s.id];
  const err = STATUS?.errors?.[s.id];
  const active = st?.services?.[s.service] === "active";
  const nodes = NODES.filter((n) => n.server_id === s.id);
  return `
    <div class="crumb"><a href="#/">全部服务器</a><span class="sep">/</span><b>${esc(s.name)}</b>
      <span class="crumb-actions">
        <button class="sm" id="btnSvrLinks">复制本机链接</button>
        <button class="sm${NEWSVR === s.id ? " on" : ""}" id="btnSvrNew">＋ 新建节点</button>
      </span>
    </div>
    <div class="svr-head">
      <div class="sh-left">
        <span class="gh-dot ${err ? "bad" : active ? "ok" : ""}"></span>
        <div>
          <div class="sh-title">${esc(s.name)} <span class="pill ${err ? "bad" : active ? "ok" : ""}">${err ? "SSH 失败" : active ? "运行中" : "未采集"}</span></div>
          <div class="gh-sub"><code>${esc(s.host)}</code> · ${esc(s.engine)} · ${nodes.length} 个节点</div>
        </div>
      </div>
      <div class="sh-right">
        <div class="gh-metrics">${metricsHtml(st, true)}</div>
        <div class="gh-services">${st ? servicesLine(st) : ""}</div>
      </div>
    </div>
    <div class="group-grid">
      ${nodes.map(nodeCard).join("") || `<div class="hint" style="padding:8px">该服务器暂无节点</div>`}
      ${newFormHtml(s)}
    </div>`;
}

/* ------------------------------------------------------------ 渲染 / 路由 */

function render() {
  const view = $("#view");
  const sid = route();
  const s = SERVERS.find((x) => x.id === sid);

  $("#crumb").innerHTML = s
    ? `<a href="#/" style="color:var(--dim)">全部服务器</a> / ${esc(s.name)}`
    : `${SERVERS.length} 台服务器 · ${NODES.length} 个节点`;
  $("#empty").hidden = true;

  if (!s) {
    view.innerHTML = overviewHtml();
    view.querySelectorAll("button[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));
    view.querySelectorAll(".srv-card").forEach((c) => (c.onclick = (e) => {
      if (e.target.tagName !== "BUTTON") go(c.dataset.sid);
    }));
    return;
  }

  view.innerHTML = detailHtml(s);
  view.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.closest(".card").dataset.id;
    b.onclick = () => action(b.dataset.act, NODES.find((x) => x.id === id));
  });
  $("#btnSvrNew").onclick = () => { NEWSVR = NEWSVR === s.id ? null : s.id; render(); };
  $("#btnSvrLinks").onclick = async () => {
    const txt = nodesOf(s.id).map((n) => n.link).join("\n");
    await navigator.clipboard.writeText(txt);
    toast(`已复制 ${nodesOf(s.id).length} 条链接`);
  };
}

const nodesOf = (sid) => NODES.filter((n) => n.server_id === sid);

async function loadAll() {
  const data = await api("/api/nodes");
  NODES = data.nodes;
  STATUS = data.status;
  SERVERS = await api("/api/servers");
  render();
  $("#stamp").textContent = STATUS ? "采集于 " + STATUS.ts : "未刷新";
}

/* ------------------------------------------------------------ 动作 */

async function action(act, n) {
  if (!n) return;
  if (act === "copy") {
    await navigator.clipboard.writeText(n.link);
    toast("链接已复制");
  } else if (act === "reach") {
    toast("探测中…");
    const r = await api("/api/reach", "POST", { id: n.id });
    toast(r.udp ? "UDP 端口，跳过 TCP 探测" : r.ok ? `TCP ${n.port} 可达 (${r.ms}ms)` : `TCP ${n.port} 不通`);
  } else if (act === "del") {
    if (!confirm(`删除节点「${n.label}」？会同时移除服务器上的入站配置（自动备份后重启）。`)) return;
    toast("处理中…");
    const r = await api("/api/node/del", "POST", { id: n.id });
    toast(r.ok ? "已删除" : "失败：" + r.err);
    if (r.ok) { EXP = null; loadAll(); }
  } else {
    EXP = EXP && EXP.id === n.id && EXP.act === act ? null : { id: n.id, act };
    render();
    if (EXP && act === "users") refreshUsers(n.id);
  }
}

async function refreshUsers(id) {
  const r = await api("/api/users", "POST", { id });
  const body = document.getElementById("usersBody_" + id);
  if (!body) return;
  if (!r.ok) { body.innerHTML = `<span style="color:var(--bad)">读取失败：${esc(r.err)}</span>`; return; }
  body.innerHTML = !r.users.length ? `<div class="hint">该入站没有用户</div>`
    : `<div class="userlist">` + r.users.map((u) => `
      <div class="user">
        <div class="min0"><div class="uid">${esc(u.id)}</div>
        <div class="hint">${esc(u.label || "无备注")} ${u.flow ? "· flow:" + esc(u.flow) : ""}</div></div>
        <div class="row"><button class="sm" onclick="copyLink('${id}','${esc(u.id)}')">链接</button>
        <button class="sm danger" onclick="delUser('${id}','${esc(u.id)}')">删除</button></div>
      </div>`).join("") + `</div>`;
}

async function addUser(id) {
  const label = (document.getElementById("newLabel_" + id) || {}).value || "";
  $("#addResult_" + id).innerHTML = `<span class="spin"></span> 写入配置并重启中…`;
  const r = await api("/api/users/add", "POST", { id, label });
  const box = $("#addResult_" + id);
  if (!r.ok) { box.innerHTML = `<span style="color:var(--bad)">失败：${esc(r.err)}</span>`; return; }
  box.innerHTML = `<div class="qrbox sm">
      <img src="/api/qr?text=${encodeURIComponent(r.link)}" alt="qr">
      <div class="qrside"><textarea id="nl_${esc(r.uuid)}" readonly>${esc(r.link)}</textarea>
      <div class="row"><button class="primary sm" onclick="copy('#nl_${esc(r.uuid)}')">复制</button></div></div>
    </div>`;
  refreshUsers(id);
}

async function delUser(id, uid) {
  if (!confirm("删除用户 " + uid + " ？")) return;
  toast("处理中…");
  const r = await api("/api/users/del", "POST", { id, uuid: uid });
  toast(r.ok ? `已删除 ${r.removed} 个用户` : "失败：" + r.err);
  refreshUsers(id);
}

async function copyLink(nodeId, uid) {
  const n = NODES.find((x) => x.id === nodeId);
  const link = buildLinkFrom({ ...n, params: { ...n.params, uuid: uid } });
  await navigator.clipboard.writeText(link);
  toast("已复制该用户的链接");
}

async function runSpeed(id) {
  $("#spdResult_" + id).innerHTML = `<span class="spin"></span> 正在穿过节点下载，约 10-40 秒…`;
  const r = await api("/api/speedtest", "POST", { id });
  const box = $("#spdResult_" + id);
  if (!r.ok) { box.innerHTML = `<span style="color:var(--bad)">失败：${esc(r.err)}</span>`; return; }
  if (r.speed === undefined) {
    box.innerHTML = `<span style="color:var(--warn)">未完成下载</span><pre class="mono dim" style="font-size:11px">${esc(JSON.stringify(r))}</pre>`;
    return;
  }
  box.innerHTML = `<div class="big">${r.speed} MB/s</div>
    <div class="hint">≈ ${r.mbps} Mbps · ${r.size_mb} MB / ${r.seconds}s · HTTP ${r.http}</div>`;
}

async function doRotate(id) {
  $("#rotOut_" + id).innerHTML = `<span class="spin"></span> 生成密钥并重启中…`;
  const r = await api("/api/rotate", "POST", { id });
  const box = $("#rotOut_" + id);
  if (!r.ok) { box.innerHTML = `<span style="color:var(--bad)">失败：${esc(r.err)}</span>`; return; }
  box.innerHTML = `
    <div class="field"><label>新公钥 pbk</label><textarea readonly>${esc(r.pbk)}</textarea></div>
    <div class="field"><label>新 shortId</label><textarea readonly>${esc(r.sid)}</textarea></div>
    <div class="field"><label>新链接</label><textarea id="rotLnk_${id}" readonly>${esc(r.link)}</textarea></div>
    <button class="primary sm" onclick="copy('#rotLnk_${id}')">复制新链接</button>`;
  loadAll();
}

async function createNode(sid) {
  const port = parseInt(document.getElementById("nPort_" + sid).value, 10);
  const label = document.getElementById("nLabel_" + sid).value;
  const sni = document.getElementById("nSni_" + sid).value;
  if (!port) return toast("填端口");
  $("#newOut_" + sid).innerHTML = `<span class="spin"></span> 克隆入站 → 校验 → 重启…`;
  const r = await api("/api/node/new", "POST", { server_id: sid, port, label, sni });
  const box = $("#newOut_" + sid);
  if (!r.ok) { box.innerHTML = `<span style="color:var(--bad)">失败：${esc(r.err)}</span>`; return; }
  box.innerHTML = `<textarea id="newLnk_${sid}" readonly>${esc(r.link)}</textarea>
    <div class="row" style="margin-top:6px"><button class="primary sm" onclick="copy('#newLnk_${sid}')">复制链接</button></div>`;
  NEWSVR = null;
  loadAll();
}

/* ------------------------------------------------------------ 工具 */

function buildLinkFrom(n) {
  const q = {
    encryption: "none", security: "reality",
    sni: n.params.sni, fp: n.params.fp || "chrome",
    pbk: n.params.pbk, sid: n.params.sid, type: "tcp",
  };
  if (n.params.flow) q.flow = n.params.flow;
  return `vless://${n.params.uuid}@${n.host}:${n.port}?${new URLSearchParams(q)}#${encodeURIComponent(n.label)}`;
}

async function copy(sel) {
  const el = document.querySelector(sel);
  await navigator.clipboard.writeText(el.value);
  toast("已复制");
}

async function rename(id) {
  const label = document.getElementById("lbl_" + id).value;
  const r = await api("/api/node/update", "POST", { id, label });
  toast(r.ok ? "已保存" : "失败：" + r.err);
  loadAll();
}

function yamlDump(obj) {
  const out = [];
  const walk = (o, indent) => {
    for (const [k, v] of Object.entries(o)) {
      const pad = "  ".repeat(indent);
      if (v && typeof v === "object" && !Array.isArray(v)) { out.push(`${pad}${k}:`); walk(v, indent + 1); }
      else if (Array.isArray(v)) out.push(`${pad}${k}: [${v.join(", ")}]`);
      else out.push(`${pad}${k}: ${typeof v === "string" && v.includes(" ") ? '"' + v + '"' : v}`);
    }
  };
  walk(obj[0], 0);
  return out.join("\n").replace(/^/, "  - ").replace(/\n {2}/g, "\n    ");
}

/* ------------------------------------------------------------ 顶栏 */

$("#btnRefresh").onclick = async () => {
  const b = $("#btnRefresh");
  b.disabled = true;
  b.innerHTML = `<span class="spin"></span> 采集`;
  await api("/api/status/refresh", "POST");
  await loadAll();
  b.disabled = false;
  b.textContent = "刷新状态";
  toast("状态已刷新");
};

$("#btnAllLinks").onclick = async () => {
  const txt = SERVERS.map((s) =>
    `# ${s.name} (${s.host})\n` + nodesOf(s.id).map((n) => n.link).join("\n")
  ).join("\n\n");
  await navigator.clipboard.writeText(txt);
  toast(`已复制 ${NODES.length} 条链接（按服务器分组）`);
};

$("#brand").onclick = () => go(null);
window.addEventListener("hashchange", () => { EXP = null; NEWSVR = null; render(); });

loadAll();
