/* ---------------- 交互 ---------------- */
$("#btnRun").onclick = run;
$("#jumpTo").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { jumpToPage(); e.target.blur(); }
});
$("#jumpTo").addEventListener("change", jumpToPage);
$("#btnPolish").onclick = togglePolish;
$("#btnSync").onclick = (e) => {
  syncOn = !syncOn;
  e.target.textContent = "同步: " + (syncOn ? "开" : "关");
};
$("#btnSrc").onclick = () => {
  showSrc = !showSrc;
  right.querySelectorAll(".para").forEach((p) =>
    p.classList.toggle("show-src", showSrc));
};
/* 顶栏快捷开关: 一键隐藏/恢复译文栏, 记住之前的布局 */
$("#btnPane").onclick = () => {
  if (prefs.layout === "pdfonly") {
    prefs.layout = prefs.layoutPrev === "pdfonly" ? "side" : (prefs.layoutPrev || "side");
  } else {
    prefs.layoutPrev = prefs.layout;
    prefs.layout = "pdfonly";
  }
  savePrefs();
  applyPrefs();
};
/* 目录大纲面板开关 */
$("#btnToc").onclick = () => {
  prefs.toc = !prefs.toc;
  savePrefs();
  applyPrefs();
};
right.addEventListener("click", (e) => {
  const p = e.target.closest(".para");
  if (p) p.classList.toggle("show-src");
});

/* ---------------- 文件选择 / 上传 ---------------- */
function openPicker() { $("#picker").classList.add("on"); loadHistory(); loadDir(""); }
function closePicker() { $("#picker").classList.remove("on"); }

function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 86400 * 30) return Math.floor(s / 86400) + " 天前";
  return new Date(ts * 1000).toLocaleDateString();
}

async function loadHistory() {
  const wrap = $("#pkHistWrap"), box = $("#pkHist");
  let items = [];
  try { items = await (await fetch("/api/history")).json(); } catch (e) {}
  if (!items.length) { wrap.style.display = "none"; return; }
  wrap.style.display = "";
  box.innerHTML = items.map((h) =>
    `<div class="row${h.exists ? "" : " gone"}" data-hfile="${esc(h.path)}">`
    + `<span class="ico">▪</span><span class="nm" title="${esc(h.path)}">${esc(h.name)}</span>`
    + `<span class="sz">${ago(h.ts)}</span>`
    + `<button class="hx" data-hx="${esc(h.path)}" title="从历史移除">×</button></div>`
  ).join("");
}

$("#pkHist").addEventListener("click", async (e) => {
  const hx = e.target.closest(".hx");
  if (hx) {
    e.stopPropagation();
    await fetch("/api/history/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: hx.dataset.hx }),
    });
    loadHistory();
    return;
  }
  const row = e.target.closest(".row[data-hfile]");
  if (row) openFile(row.dataset.hfile);
});

async function loadDir(dir) {
  const box = $("#pkList");
  box.innerHTML = `<div class="row"><span class="nm" style="color:var(--dim)">加载中…</span></div>`;
  let d;
  try {
    const r = await fetch("/api/browse?dir=" + encodeURIComponent(dir));
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    d = await r.json();
  } catch (e) {
    box.innerHTML = `<div class="row"><span class="nm" style="color:#ff7a7a">${esc(String(e))}</span></div>`;
    return;
  }

  $("#pkPath").textContent = d.cwd || "可访问的目录";
  const rows = [];
  if (d.parent) rows.push(`<div class="row" data-dir="${esc(d.parent)}">`
    + `<span class="ico">↑</span><span class="nm">上一级</span></div>`);
  for (const x of d.dirs) rows.push(`<div class="row" data-dir="${esc(x.path)}">`
    + `<span class="ico">▸</span><span class="nm">${esc(x.name)}</span></div>`);
  for (const f of d.files) rows.push(`<div class="row" data-file="${esc(f.path)}">`
    + `<span class="ico">▪</span><span class="nm">${esc(f.name)}</span>`
    + `<span class="sz">${(f.size / 1048576).toFixed(1)} MB</span></div>`);
  if (!rows.length) rows.push(`<div class="row"><span class="nm" style="color:var(--dim)">`
    + `此目录下没有 PDF / EPUB / TXT，可点「上传文件」或拖入</span></div>`);
  box.innerHTML = rows.join("");
}

$("#pkList").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  if (row.dataset.dir) loadDir(row.dataset.dir);
  else if (row.dataset.file) openFile(row.dataset.file);
});

async function openFile(path) {
  const serial = ++fileSerial, previous = right.innerHTML;
  // 打开新文件前停掉当前翻译流并快照当前标签, 避免 busy 提示覆盖快照
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false; polishing = false;
  snapshotTab();
  closePicker();
  setBusyProg("正在解析 " + path.split(/[/\\]/).pop() + " …", null);
  try {
    const r = await fetch("/api/open?path=" + encodeURIComponent(path), { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    if (serial === fileSerial) await reload(await r.json());
  } catch (e) {
    if (serial === fileSerial) {
      right.innerHTML = previous;
      hideBusy();
      $("#runStatus").textContent = "打开失败：" + e;
    }
  }
}

function uploadFile(file) {
  if (!file || !/\.(pdf|epub|txt)$/i.test(file.name)) { setBusy("仅支持 PDF / EPUB / TXT 文件", true); return; }
  if (file.size > 200 * 1024 * 1024) { setBusy("文件过大（上限 200 MB）", true); return; }
  const serial = ++fileSerial, previous = right.innerHTML;
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false; polishing = false;
  snapshotTab();
  closePicker();

  const mb = (file.size / 1048576).toFixed(1);
  setBusyProg(`正在上传 ${file.name}（${mb} MB）`, 0);

  // 用 XHR 获取真实上传进度(fetch 不支持 upload progress)
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable && serial === fileSerial) {
      setBusyProg(`正在上传 ${file.name}（${mb} MB）`,
                  (e.loaded / e.total) * 100);
    }
  };
  xhr.upload.onload = () => {
    if (serial === fileSerial) setBusyProg("上传完成，正在解析版面 …", null);
  };
  xhr.onload = async () => {
    if (serial !== fileSerial) return;
    if (xhr.status < 400) {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      await reload(data);
      return;
    }
    let msg = xhr.status;
    try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
    right.innerHTML = previous;
    hideBusy();
    $("#runStatus").textContent = "上传失败：" + msg;
  };
  xhr.onerror = () => {
    if (serial !== fileSerial) return;
    right.innerHTML = previous;
    hideBusy();
    $("#runStatus").textContent = "上传失败：网络错误";
  };
  const fd = new FormData();
  fd.append("file", file);
  xhr.send(fd);
}

/* 浮动加载卡片(右下角): 译文栏隐藏的布局下也能看到打开进度 */
let busyErrTimer = null;
function showBusyOverlay(html, isErr) {
  const el = $("#busy");
  el.innerHTML = html;
  el.classList.toggle("err", !!isErr);
  el.classList.add("on");
  clearTimeout(busyErrTimer);
  if (isErr) busyErrTimer = setTimeout(hideBusy, 6000);   // 错误提示短暂停留
}
function hideBusy() {
  clearTimeout(busyErrTimer);
  $("#busy").classList.remove("on");
}

function setBusy(msg, err) {
  showBusyOverlay(esc(msg), err);
  right.innerHTML = `<div class="empty"${err ? ' style="color:#ff7a7a"' : ""}>${esc(msg)}</div>`;
}

/* 带进度的打开提示; pct 为 null 时显示不定态动画 */
function setBusyProg(msg, pct) {
  const bar = pct == null
    ? `<div class="indet"></div>`
    : `<div style="width:${Math.min(100, Math.max(0, pct))}%"></div>`;
  const html = `<div class="busywrap">`
    + `<div class="busymsg">${esc(msg)}</div>`
    + `<div class="busybar">${bar}</div></div>`;
  showBusyOverlay(html, false);
  right.innerHTML = `<div class="empty">${html}</div>`;
}

const fmtMB = (b) => (b == null ? "" : (b / 1048576).toFixed(1) + " MB");

async function reload(data) {
  hideBusy();
  addOrActivateTab(data);
}

$("#btnOpen").onclick = openPicker;
$("#pkClose").onclick = closePicker;
$("#pkUp").onclick = () => $("#pkFile").click();
$("#pkOpenUrl").onclick = openUrl;
$("#pkUrl").addEventListener("keydown", (e) => {
  if (e.key === "Enter") openUrl();
});

async function openUrl() {
  const inp = $("#pkUrl"), url = inp.value.trim();
  if (!url) return;
  const serial = ++fileSerial, previous = right.innerHTML;
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false; polishing = false;
  snapshotTab();
  closePicker();

  const task = Math.random().toString(36).slice(2);
  setBusyProg("正在获取链接内容 …", null);
  const timer = setInterval(async () => {
    if (serial !== fileSerial) return;
    try {
      const p = await (await fetch("/api/progress?task=" + task)).json();
      if (p.stage === "download") {
        setBusyProg(p.total
          ? `正在下载 ${fmtMB(p.done)} / ${fmtMB(p.total)}`
          : `正在下载 ${fmtMB(p.done)} …`,
          p.total ? (p.done / p.total) * 100 : null);
      } else if (p.stage === "convert") {
        setBusyProg("网页转 PDF 中，可能需要十几秒 …", null);
      } else if (p.stage === "parse") {
        setBusyProg("正在解析版面 …", null);
      }
    } catch (e) { /* 轮询失败忽略 */ }
  }, 350);

  try {
    const r = await fetch("/api/open-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, task }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    inp.value = "";
    if (serial === fileSerial) await reload(await r.json());
  } catch (e) {
    if (serial === fileSerial) {
      right.innerHTML = previous;
      hideBusy();
      $("#runStatus").textContent = "打开失败：" + e;
    }
  } finally { clearInterval(timer); }
}
$("#pkAddRoot").onclick = addRoot;
$("#pkRoot").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addRoot();
});

async function addRoot() {
  const inp = $("#pkRoot"), v = inp.value.trim();
  if (!v) return;
  const pathEl = $("#pkPath");
  try {
    const r = await fetch("/api/roots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: v }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    inp.value = "";
    pathEl.style.color = "";
    loadDir(d.dir || v);
  } catch (e) {
    pathEl.style.color = "#ff7a7a";
    pathEl.textContent = "添加失败：" + e;
  }
}
$("#pkFile").onchange = (e) => uploadFile(e.target.files[0]);
$("#picker").addEventListener("click", (e) => {
  if (e.target.id === "picker") closePicker();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePicker();
});

/* 拖放打开 */
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  if (++dragDepth === 1) $("#drop").classList.add("on");
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", (e) => {
  e.preventDefault();
  if (--dragDepth <= 0) { dragDepth = 0; $("#drop").classList.remove("on"); }
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0; $("#drop").classList.remove("on");
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) uploadFile(f);
});

