/* ---------------- 文库视图 ---------------- */
let LIB = null;                                  // {folders, tags, documents}
let libScope = { kind: "all", id: 0, name: "" }; // all | none | folder | tag
let libQuery = "";
let libSel = new Set();                          // 批量操作选中的文档 id

/* 书架: 文库铺满整窗(封面网格), 侧栏那套布局原样不动。
   返回阅读后回到原来的侧栏形态, 阅读区照旧。 */
const isShelf = () => document.body.classList.contains("shelf");

function setShelf(on) {
  document.body.classList.toggle("shelf", !!on);
  if (on) {
    // 书架必然要先打开文库
    document.body.classList.add("lib");
    $("#btnLib").classList.add("on");
    prefs.lib = true;
  }
  prefs.libShelf = !!on;
  savePrefs();
  // 先同步控件再取数: loadLibrary 是异步的, 放在它之后会被旧状态覆盖
  syncLibControls();
  if (on) loadLibrary(); else renderLibrary();
}

/* 文库为侧栏, 与阅读区共存; 开关只控制侧栏显隐, 状态记入偏好 */
function showLibrary() {
  document.body.classList.add("lib");
  $("#btnLib").classList.add("on");
  prefs.lib = true;
  savePrefs();
  loadLibrary();
}
function showReader() {
  document.body.classList.remove("lib");
  $("#btnLib").classList.remove("on");
  prefs.lib = false;
  savePrefs();
}

async function loadLibrary() {
  try {
    const d = await (await fetch("/api/library")).json();
    LIB = d && Array.isArray(d.documents) ? d : null;
  } catch (e) { LIB = null; }
  if (!LIB) LIB = { folders: [], tags: [], documents: [] };
  renderLibrary();
}

/* 文档行。三种呈现:
   - 列表(侧栏默认): 一行一篇约 33px, 用于快速定位
   - 详情(侧栏): 卡片式, 文件名/划线/文件夹等信息全摊开
   - 书架: 整窗铺开的封面网格, 用于浏览和整理
   书架由 body.shelf 控制, 与侧栏的 列表/详情 设置互不干扰。 */
function docRow(d) {
  const date = d.last_opened ? new Date(d.last_opened * 1000).toLocaleDateString() : "";
  const read = d.progress > 0 && d.pages > 0;
  const pct = read ? Math.min(100, Math.round(d.progress / d.pages * 100)) : 0;
  const checked = libSel.has(d.id) ? " checked" : "";
  const box = `<input type="checkbox" class="libck" data-ck="${d.id}"${checked}`
    + ` title="选中以批量操作" />`;

  if (isShelf()) {
    // 书架卡片: 封面 + 标题 + 元信息; 封面加载失败(EPUB/TXT 或渲染不了)
    // 退回文字占位, 不留破图
    const ext = (d.name.split(".").pop() || "").toUpperCase();
    return `<div class="card${d.exists ? "" : " gone"}"`
      + `${libSel.has(d.id) ? ' data-sel="1"' : ""} data-id="${d.id}">`
      + `<div class="cover" data-open="${d.id}">`
      + (d.exists
        ? `<img loading="lazy" src="/api/thumb/${d.id}.png" alt=""`
          + ` onerror="this.remove()" />`
        : "")
      + `<span class="ph">${esc(ext || "DOC")}</span>`
      + (read ? `<span class="bar"><i style="width:${pct}%"></i></span>` : "")
      + `</div>`
      + `<div class="meta">`
      + `<div class="t" data-open="${d.id}" title="${esc(d.title || d.name)}">`
      + `${esc(d.title || d.name)}</div>`
      + `<div class="m">${esc(date)}`
      + (read ? ` · ${pct}%` : "")
      + (d.highlights ? ` · ✎${d.highlights}` : "")
      + (d.exists ? "" : " · 文件已不在") + `</div>`
      + `<div class="tagsrow">`
      + d.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")
      + `<button class="tagbtn" data-tags="${d.id}" title="编辑标签">＋</button>`
      + `</div></div>`
      + `<div class="pick">${box}</div>`
      + `<button class="rm ghost" data-rm="${d.id}" title="从库中移除">×</button>`
      + `</div>`;
  }

  if (prefs.libView !== "detail") {
    // 列表行: 勾选框 + 标题 + 标签 + 右侧次要信息, 操作悬停浮现
    return `<div class="doc dense${d.exists ? "" : " gone"}"`
      + `${libSel.has(d.id) ? ' data-sel="1"' : ""}>`
      + box
      + `<span class="t" data-open="${d.id}" title="${esc(d.path)}">`
      + `${esc(d.title || d.name)}</span>`
      + d.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")
      + `<span class="grow"></span>`
      + (read ? `<span class="pct" title="读到 ${d.progress}/${d.pages} 页">${pct}%</span>` : "")
      + (d.highlights ? `<span class="hl2" title="${d.highlights} 条划线">✎${d.highlights}</span>` : "")
      + `<span class="dt">${esc(date)}</span>`
      + (d.exists ? "" : `<span class="warn2" title="文件已不在原位置">!</span>`)
      + `<span class="rowops">`
      + `<button class="tagbtn" data-tags="${d.id}" title="编辑标签">标签</button>`
      + `<button class="ghost" data-rm="${d.id}" title="从库中移除(不删磁盘文件)">移除</button>`
      + `</span></div>`;
  }

  const opts = [`<option value="">未分类</option>`].concat(LIB.folders.map((f) =>
    `<option value="${f.id}"${d.folder_id === f.id ? " selected" : ""}>${esc(f.name)}</option>`
  )).join("");
  return `<div class="doc${d.exists ? "" : " gone"}"`
    + `${libSel.has(d.id) ? ' data-sel="1"' : ""}>`
    + `<div class="headrow">${box}`
    + `<div class="t" data-open="${d.id}" title="${esc(d.path)}">${esc(d.title || d.name)}</div></div>`
    + `<div class="m">${esc(d.name)} · ${date} · ${d.highlights} 条划线`
    + (read ? ` · 读到 ${d.progress}/${d.pages} 页` : "")
    + (d.exists ? "" : " · 文件已不在原位置") + `</div>`
    + `<div class="tagsrow">${d.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}`
    + `<button class="tagbtn" data-tags="${d.id}" title="编辑标签">＋标签</button></div>`
    + `<div class="ops"><button data-open="${d.id}"${d.exists ? "" : " disabled"}>打开</button>`
    + `<select data-move="${d.id}" title="移动到文件夹">${opts}</select>`
    + `<button class="ghost" data-rm="${d.id}" title="从库中移除(不删磁盘文件)">移除</button>`
    + `</div></div>`;
}

/* 排序: 服务端固定按 last_opened 返回, 这里在前端再排一次 */
const LIB_SORTS = {
  opened: { name: "最近打开", fn: (a, b) => (b.last_opened || 0) - (a.last_opened || 0) },
  added: { name: "最近添加", fn: (a, b) => (b.added || 0) - (a.added || 0) },
  title: { name: "标题", fn: (a, b) => (a.title || a.name).localeCompare(b.title || b.name, "zh") },
  progress: { name: "阅读进度", fn: (a, b) => (b.progress / (b.pages || 1)) - (a.progress / (a.pages || 1)) },
};

function libSorted(docs) {
  const s = LIB_SORTS[prefs.libSort] || LIB_SORTS.opened;
  return docs.slice().sort(s.fn);
}

function renderLibrary() {
  if (!LIB) return;
  const q = libQuery.trim().toLowerCase();
  $("#cntAll").textContent = LIB.documents.length;
  $("#cntNone").textContent = LIB.documents.filter((d) => !d.folder_id).length;

  $("#libFolders").innerHTML = LIB.folders.map((f) => {
    const cnt = LIB.documents.filter((d) => d.folder_id === f.id).length;
    return `<div class="libitem" data-folder="${f.id}">`
      + `<span class="nm">${esc(f.name)}</span><span class="cnt">${cnt}</span>`
      + `<button class="fx" data-frename="${f.id}" title="重命名">✎</button>`
      + `<button class="fx" data-fdel="${f.id}" title="删除文件夹">×</button></div>`;
  }).join("") || `<div class="libitem" style="color:var(--dim);cursor:default">`
    + `<span class="nm">暂无文件夹</span></div>`;

  $("#libTags").innerHTML = LIB.tags.map((t) =>
    `<div class="libitem" data-tag="${esc(t.name)}">`
    + `<span class="nm"># ${esc(t.name)}</span><span class="cnt">${t.count}</span></div>`
  ).join("") || `<div class="libitem" style="color:var(--dim);cursor:default">`
    + `<span class="nm">暂无标签</span></div>`;

  document.querySelectorAll("#libSide .libitem").forEach((el) => {
    const on = (el.dataset.scope && libScope.kind === el.dataset.scope)
      || (el.dataset.folder && libScope.kind === "folder" && +el.dataset.folder === libScope.id)
      || (el.dataset.tag && libScope.kind === "tag" && el.dataset.tag === libScope.name);
    el.classList.toggle("on", !!on);
  });

  let docs = LIB.documents.slice();
  if (libScope.kind === "none") docs = docs.filter((d) => !d.folder_id);
  else if (libScope.kind === "folder") docs = docs.filter((d) => d.folder_id === libScope.id);
  else if (libScope.kind === "tag") docs = docs.filter((d) => d.tags.includes(libScope.name));
  if (q) docs = docs.filter((d) =>
    (d.title + " " + d.name + " " + d.tags.join(" ")).toLowerCase().includes(q));
  docs = libSorted(docs);
  // 选中集合只保留当前可见的文档: 换了筛选范围后不该残留看不见的选中项
  const visible = new Set(docs.map((d) => d.id));
  for (const id of [...libSel]) if (!visible.has(id)) libSel.delete(id);

  $("#libList").innerHTML = docs.map(docRow).join("")
    || `<div class="empty">这里没有文档<br /><br /><span style="font-size:12px">`
    + `点右上「导入文件」，或直接把 PDF / EPUB 拖进窗口</span></div>`;
  $("#libCount").textContent = docs.length
    ? `${docs.length} 篇` + (q || libScope.kind !== "all" ? "（已筛选）" : "")
    : "";
  renderLibBulk(docs.length);
  syncLibControls();
}

/* 批量操作条: 有选中时才出现 */
function renderLibBulk(total) {
  const bar = $("#libBulk");
  if (!bar) return;
  const n = libSel.size;
  bar.classList.toggle("on", n > 0);
  if (!n) return;
  const opts = [`<option value="">移动到…</option>`,
                `<option value="0">未分类</option>`]
    .concat(LIB.folders.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`))
    .join("");
  bar.innerHTML = `<span class="n">已选 ${n} 篇</span>`
    + `<select id="libMoveSel" title="把选中的文档移动到文件夹">${opts}</select>`
    + `<button class="ghost" id="libSelAll">全选 ${total} 篇</button>`
    + `<button class="ghost" id="libSelNone">取消选择</button>`
    + `<span class="grow"></span>`
    + `<button class="ghost" id="libBulkRm">移除</button>`;
}

/* 同步顶部的排序 / 列表·详情 / 书架控件显示 */
function syncLibControls() {
  const sort = $("#libSort");
  if (sort) sort.value = prefs.libSort || "opened";
  const view = prefs.libView === "detail" ? "detail" : "list";
  document.querySelectorAll("#libViewSeg button").forEach((b) =>
    b.classList.toggle("on", b.dataset.v === view));
  // 书架本身就是网格, 列表/详情 在那里没有意义
  const seg = $("#libViewSeg");
  if (seg) seg.style.display = isShelf() ? "none" : "";
  const shelf = $("#btnShelf");
  if (shelf) {
    // 进入书架后按钮的语义变成"回到哪", 而不是"离开什么"
    shelf.classList.toggle("on", isShelf());
    shelf.textContent = isShelf() ? "返回阅读" : "书架";
    shelf.title = isShelf()
      ? "回到阅读视图（Esc）"
      : "以封面网格铺满窗口浏览文库";
  }
}

const libPost = (url, body) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

async function openLibDoc(id) {
  const serial = ++fileSerial;
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false; polishing = false;
  $("#btnRun").textContent = "开始翻译";
  // 书架是"挑文档"的视图, 挑完就该回到阅读区
  if (isShelf()) setShelf(false);
  snapshotTab();
  // 已打开的文档后端直接激活(不重新解析), 300ms 内完成则不显示 busy。
  // 注意用浮动卡片而不是写右栏: 右栏是共享画布, 切标签时会被快照存档
  const busyTimer = setTimeout(() =>
    showBusyOverlay(`<div class="busywrap"><div class="busymsg">正在打开文档 …</div>`
      + `<div class="busybar"><div class="indet"></div></div></div>`, false), 300);
  try {
    const r = await libPost("/api/library/open", { id });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const data = await r.json();
    if (serial !== fileSerial) return;
    const existing = tabs.find((x) => x.id === data.doc);
    if (existing && existing.leftHTML != null) {
      existing.info = data;
      if (data.doc === activeTab) restoreTab(existing);
      else switchTab(data.doc);
    } else {
      await reload(data);
    }
  } catch (e) {
    if (serial === fileSerial) {
      $("#runStatus").textContent = "打开失败：" + e;
    }
  } finally {
    clearTimeout(busyTimer);
    hideBusy();
  }
}

/* 侧栏: 范围筛选 / 文件夹重命名与删除 */
$("#libSide").addEventListener("click", (e) => {
  const rn = e.target.closest("[data-frename]");
  if (rn) { renameFolderInline(rn.closest(".libitem")); return; }
  const del = e.target.closest("[data-fdel]");
  if (del) { deleteFolder(+del.dataset.fdel); return; }
  const folderEl = e.target.closest("[data-folder]");
  if (folderEl) {
    libScope = { kind: "folder", id: +folderEl.dataset.folder, name: "" };
    renderLibrary(); return;
  }
  const tagEl = e.target.closest("[data-tag]");
  if (tagEl) { libScope = { kind: "tag", id: 0, name: tagEl.dataset.tag }; renderLibrary(); return; }
  const scopeEl = e.target.closest("[data-scope]");
  if (scopeEl) { libScope = { kind: scopeEl.dataset.scope, id: 0, name: "" }; renderLibrary(); }
});

$("#btnNewFolder").onclick = () => {
  const box = $("#libFolders");
  if (box.querySelector("input")) return;
  const div = document.createElement("div");
  div.className = "libitem";
  div.innerHTML = `<input placeholder="文件夹名称，回车确认" style="flex:1;font-size:12.5px" />`;
  box.appendChild(div);
  const inp = div.querySelector("input");
  inp.focus();
  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const name = inp.value.trim();
    div.remove();
    if (!name) return;
    const r = await libPost("/api/library/folders", { name });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $("#runStatus").textContent = "新建文件夹失败：" + (d.detail || r.status);
    }
    loadLibrary();
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter") inp.blur();
    if (e.key === "Escape") { done = true; div.remove(); }
  };
  inp.onblur = commit;
};

function renameFolderInline(item) {
  const id = +item.dataset.folder;
  const f = (LIB.folders || []).find((x) => x.id === id);
  if (!f || item.querySelector("input")) return;
  const nm = item.querySelector(".nm");
  const old = f.name;
  nm.textContent = "";
  const inp = document.createElement("input");
  inp.value = old;
  inp.style.cssText = "width:100%;font-size:12.5px;padding:2px 5px";
  nm.appendChild(inp);
  inp.focus();
  inp.select();
  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const name = inp.value.trim();
    if (name && name !== old) {
      const r = await libPost("/api/library/folders/rename", { id, name });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        $("#runStatus").textContent = "重命名失败：" + (d.detail || r.status);
      }
    }
    loadLibrary();
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter") inp.blur();
    if (e.key === "Escape") { done = true; loadLibrary(); }
  };
  inp.onblur = commit;
  inp.onclick = (e) => e.stopPropagation();
}

async function deleteFolder(id) {
  const f = (LIB.folders || []).find((x) => x.id === id);
  if (!confirm(`删除文件夹「${f ? f.name : ""}」？\n其中文档会移回未分类，文档与划线保留。`)) return;
  await libPost("/api/library/folders/delete", { id });
  if (libScope.kind === "folder" && libScope.id === id) {
    libScope = { kind: "all", id: 0, name: "" };
  }
  loadLibrary();
}

/* 文档列表: 打开 / 标签编辑 / 移除 / 移动文件夹 / 勾选 */
$("#libList").addEventListener("click", async (e) => {
  const ck = e.target.closest("[data-ck]");
  if (ck) {
    const id = +ck.dataset.ck;
    if (ck.checked) libSel.add(id); else libSel.delete(id);
    // 只刷新批量条与该行选中态, 不整体重绘(免得把勾选框的焦点弄丢)
    const row = ck.closest(".doc");
    if (row) row.toggleAttribute("data-sel", ck.checked);
    renderLibBulk(LIB.documents.length);
    return;
  }
  const tagBtn = e.target.closest("[data-tags]");
  if (tagBtn) { editTagsInline(tagBtn); return; }
  const rm = e.target.closest("[data-rm]");
  if (rm) {
    const id = +rm.dataset.rm;
    const d = LIB.documents.find((x) => x.id === id);
    if (!confirm(`从库中移除「${d ? (d.title || d.name) : ""}」？\n磁盘文件不会删除，该文档的划线记录会被清除。`)) return;
    await libPost("/api/library/documents/remove", { id });
    libSel.delete(id);
    loadLibrary();
    return;
  }
  const openBtn = e.target.closest("[data-open]");
  if (openBtn && !openBtn.disabled) openLibDoc(+openBtn.dataset.open);
});

/* 批量操作条 */
$("#libBulk").addEventListener("click", async (e) => {
  if (e.target.id === "libSelNone") {
    libSel.clear();
    renderLibrary();
    return;
  }
  if (e.target.id === "libSelAll") {
    document.querySelectorAll("#libList [data-ck]").forEach((c) => libSel.add(+c.dataset.ck));
    renderLibrary();
    return;
  }
  if (e.target.id === "libBulkRm") {
    const n = libSel.size;
    if (!n) return;
    if (!confirm(`从库中移除选中的 ${n} 篇？\n磁盘文件不会删除，这些文档的划线记录会被清除。`)) return;
    for (const id of [...libSel]) {
      await libPost("/api/library/documents/remove", { id });
    }
    libSel.clear();
    loadLibrary();
  }
});

$("#libBulk").addEventListener("change", async (e) => {
  if (e.target.id !== "libMoveSel" || e.target.value === "") return;
  const folder = e.target.value === "0" ? null : +e.target.value;
  for (const id of [...libSel]) {
    await libPost("/api/library/documents/move", { id, folder_id: folder });
  }
  libSel.clear();
  loadLibrary();
});

/* 排序 / 列表·详情 / 书架 */
$("#libSort").addEventListener("change", (e) => {
  prefs.libSort = e.target.value;
  savePrefs();
  renderLibrary();
});
$("#libViewSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-v]");
  if (!b) return;
  prefs.libView = b.dataset.v;
  savePrefs();
  renderLibrary();
});
$("#btnShelf").onclick = () => setShelf(!isShelf());
/* Esc 返回阅读(弹窗开着时让弹窗自己先处理 Esc) */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !isShelf()) return;
  if ($("#picker").classList.contains("on")) return;
  if ($("#cfg").classList.contains("on")) return;
  if ($("#dup").classList.contains("on")) return;
  setShelf(false);
});

$("#libList").addEventListener("change", async (e) => {
  const mv = e.target.closest("[data-move]");
  if (!mv) return;
  await libPost("/api/library/documents/move", {
    id: +mv.dataset.move, folder_id: mv.value ? +mv.value : null,
  });
  loadLibrary();
});

function editTagsInline(btn) {
  const id = +btn.dataset.tags;
  const d = LIB.documents.find((x) => x.id === id);
  if (!d) return;
  const row = btn.closest(".tagsrow");
  row.innerHTML = `<input class="tagedit" value="${esc(d.tags.join(", "))}"`
    + ` placeholder="逗号分隔多个标签，回车保存" />`;
  const inp = row.querySelector("input");
  inp.focus();
  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const tags = inp.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    await libPost("/api/library/documents/tags", { id, tags });
    loadLibrary();
  };
  inp.onkeydown = (e) => {
    if (e.key === "Enter") inp.blur();
    if (e.key === "Escape") { done = true; loadLibrary(); }
  };
  inp.onblur = commit;
}

$("#libSearch").addEventListener("input", (e) => {
  libQuery = e.target.value;
  renderLibrary();
});
$("#btnLib").onclick = () =>
  document.body.classList.contains("lib") ? showReader() : showLibrary();
$("#btnImport").onclick = () => openPicker();

