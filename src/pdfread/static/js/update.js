/* ---------------- 版本更新 ----------------
   启动后静默检查 GitHub 最新 Release, 有新版则顶栏出现「新版」按钮;
   点开弹窗可以看更新说明、仅下载、或一键更新(下载+校验+确认后安装)。

   依赖前面已加载的 state.js($/esc) 与 prefs.js(prefs/savePrefs);
   必须排在 prefs.js 之后、启动序列之前(见 index.html 的 script 顺序)。
   安装流程本身在后端 updater.py, 这里只负责交互与进度展示。 */

let UPDATE = null;
async function checkUpdate(force) {
  try {
    const r = await fetch("/api/update/check" + (force ? "?force=1" : ""));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    if (!d || typeof d !== "object" || (!d.latest && !d.error)) throw new Error("bad payload");
    UPDATE = d;
  } catch (e) { return false; }   // 离线静默, 保留旧 UPDATE 不清空按钮状态
  if (UPDATE && UPDATE.update_result) {
    const r = UPDATE.update_result;
    $("#runStatus").textContent = r.ok
      ? `已自动更新到 v${r.version}`
      : `上次自动更新未成功（目标 v${r.version}），日志见 ${r.log}`;
  }
  const btn = $("#btnUpdate");
  if (UPDATE && UPDATE.newer && UPDATE.latest !== prefs.skipVersion) {
    btn.style.display = "";
    btn.title = `发现新版本 v${UPDATE.latest}（当前 v${UPDATE.current}）`;
  } else {
    btn.style.display = "none";
  }
  return true;
}
function openUpdateDialog() {
  if (!UPDATE || !UPDATE.newer) return;
  $("#updVer").textContent = `v${UPDATE.latest}`;
  $("#updNotes").textContent = UPDATE.notes || "（无更新说明）";
  const h = $("#updHint");
  const block = UPDATE.apply_block || "";
  $("#updApply").style.display = block ? "none" : "";
  h.textContent = `当前版本 v${UPDATE.current}` + (block ? `；无法一键更新：${block}` : "");
  h.className = "hint";
  $("#upd").classList.add("on");
}
$("#btnUpdate").onclick = openUpdateDialog;
$("#updClose").onclick = () => $("#upd").classList.remove("on");
$("#upd").addEventListener("click", (e) => {
  if (e.target.id === "upd") $("#upd").classList.remove("on");
});
$("#updSkip").onclick = () => {
  prefs.skipVersion = UPDATE.latest;
  savePrefs();
  $("#upd").classList.remove("on");
  $("#btnUpdate").style.display = "none";
};
$("#updPage").onclick = () => window.open(UPDATE.page_url, "_blank");

$("#btnProxySave").onclick = async () => {
  const h = $("#updCheckHint");
  try {
    const r = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy: $("#cfgProxy").value.trim() }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    h.textContent = "代理已保存，立即生效";
    h.className = "hint ok";
  } catch (e) {
    h.textContent = "保存失败：" + e;
    h.className = "hint bad";
  }
};

/* 手动检查更新: 强制刷新(忽略 6 小时缓存); 即使之前忽略过该版本也照样弹窗 */
$("#btnCheckUpd").onclick = async () => {
  const h = $("#updCheckHint");
  h.textContent = "正在检查更新…";
  h.className = "hint";
  const ok = await checkUpdate(true);
  if (!ok) {
    h.textContent = "检查失败：网络异常或 GitHub 无法访问";
    h.className = "hint bad";
    return;
  }
  if (!UPDATE || (!UPDATE.latest && UPDATE.error)) {
    h.textContent = "检查失败：" + ((UPDATE && UPDATE.error) || "网络错误");
    h.className = "hint bad";
    return;
  }
  $("#verNow").textContent = "版本 v" + (UPDATE.current || "?");
  if (UPDATE.newer) {
    h.textContent = "";
    openUpdateDialog();
  } else {
    h.textContent = `已是最新版本（v${UPDATE.current}）`;
    h.className = "hint ok";
  }
};
$("#updDl").onclick = async () => {
  const h = $("#updHint");
  if (!UPDATE.asset_url) {
    h.textContent = "该版本没有当前平台的安装包，请到发布页下载";
    h.className = "hint bad";
    return;
  }
  h.textContent = `正在下载 ${UPDATE.asset_name} …`;
  h.className = "hint";
  try {
    const r = await fetch("/api/update/download", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    h.textContent = `已下载 v${d.version}：${d.path}`
      + " —— 退出应用后用新文件替换即可，配置与缓存不受影响";
    h.className = "hint ok";
  } catch (e) {
    h.textContent = "下载失败：" + e;
    h.className = "hint bad";
  }
};

/* 一键更新: 先下载并校验(带进度条), 用户确认后才启动安装;
   取消则什么都不会发生, 已下载的文件留着, 下次点击直接复用 */
let updBusy = false;
$("#updApply").onclick = async () => {
  if (updBusy) return;
  const h = $("#updHint"), bar = $("#updBar"), fill = $("#updBar > div");
  if (!UPDATE.asset_url) {
    h.textContent = "该版本没有当前平台的安装包，请到发布页下载";
    h.className = "hint bad";
    return;
  }
  updBusy = true;
  bar.style.display = "";
  const task = Math.random().toString(36).slice(2);
  const timer = setInterval(async () => {
    try {
      const p = await (await fetch("/api/progress?task=" + task)).json();
      if (p.stage === "download") {
        if (p.total) {
          const pct = Math.round(p.done / p.total * 100);
          fill.style.width = pct + "%";
          h.textContent = `正在下载 ${UPDATE.asset_name} … ${pct}%`
            + `（${(p.done / 1048576).toFixed(1)} / ${(p.total / 1048576).toFixed(1)} MB）`;
        } else {
          fill.className = "indet";
          h.textContent = `正在下载 ${UPDATE.asset_name} …`;
        }
      }
    } catch (e) { /* 轮询失败忽略 */ }
  }, 400);
  try {
    const r = await fetch("/api/update/download?task=" + task, { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    clearInterval(timer);
    bar.style.display = "none";
    if (!confirm(`v${d.version} 已下载并通过校验。\n应用将退出并自动完成安装和重启。`
                 + "（配置、翻译缓存、文库、划线全部保留）\n\n现在开始更新？")) {
      h.textContent = "已下载，随时可以再点「一键更新」完成安装";
      h.className = "hint ok";
      return;
    }
    h.textContent = "正在准备安装 …";
    h.className = "hint";
    const ra = await fetch("/api/update/apply", { method: "POST" });
    if (!ra.ok) throw new Error((await ra.json()).detail || ra.status);
    const a = await ra.json();
    // 服务端已自行安排退出, 此页面随后变为静态信息页
    if (es) { es.close(); es = null; }
    $("#upd").classList.remove("on");
    $("#bye").classList.add("on");
    document.title = "pdfread 更新中";
    $("#bye .t").textContent = "正在更新，通常 10–30 秒后自动重启";
    $("#bye .s").innerHTML =
      `若没有自动启动：<br>1. 手动打开安装目录的 pdfread：`
      + `<b>${esc(a.install_dir || "")}</b><br>`
      + `2. 仍不行就到发布页手动下载：`
      + `<a href="${esc(UPDATE.page_url)}" target="_blank">${esc(UPDATE.page_url)}</a><br>`
      + `3. 更新日志（排查用）：${esc(a.log || "")}`;
  } catch (e) {
    h.textContent = "更新失败：" + e;
    h.className = "hint bad";
  } finally {
    clearInterval(timer);
    bar.style.display = "none";
    updBusy = false;
  }
};
/* 启动时的静默检查放在 boot.js, 这里只定义行为 */
