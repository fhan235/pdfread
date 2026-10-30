/* ---------------- 文库视图 ---------------- */
let LIB = null;                                  // {folders, tags, documents}
let libScope = { kind: "all", id: 0, name: "" }; // all | none | folder | tag
let libQuery = "";

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

function docRow(d) {
  const opts = [`<option value="">未分类</option>`].concat(LIB.folders.map((f) =>
    `<option value="${f.id}"${d.folder_id === f.id ? " selected" : ""}>${esc(f.name)}</option>`
  )).join("");
  const date = d.last_opened ? new Date(d.last_opened * 1000).toLocaleDateString() : "";
  return `<div class="doc${d.exists ? "" : " gone"}">`
    + `<div class="t" data-open="${d.id}" title="${esc(d.path)}">${esc(d.title || d.name)}</div>`
    + `<div class="m">${esc(d.name)} · ${date} · ${d.highlights} 条划线`
    + (d.progress > 0 && d.pages > 0 ? ` · 读到 ${d.progress}/${d.pages} 页` : "")
    + (d.exists ? "" : " · 文件已不在原位置") + `</div>`
    + `<div class="tagsrow">${d.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}`
    + `<button class="tagbtn" data-tags="${d.id}" title="编辑标签">＋标签</button></div>`
    + `<div class="ops"><button data-open="${d.id}"${d.exists ? "" : " disabled"}>打开</button>`
    + `<select data-move="${d.id}" title="移动到文件夹">${opts}</select>`
    + `<button class="ghost" data-rm="${d.id}" title="从库中移除(不删磁盘文件)">移除</button>`
    + `</div></div>`;
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
  $("#libList").innerHTML = docs.map(docRow).join("")
    || `<div class="empty">这里没有文档<br /><br /><span style="font-size:12px">`
    + `点右上「导入文件」，或直接把 PDF / EPUB 拖进窗口</span></div>`;
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

/* 文档列表: 打开 / 标签编辑 / 移除 / 移动文件夹 */
$("#libList").addEventListener("click", async (e) => {
  const tagBtn = e.target.closest("[data-tags]");
  if (tagBtn) { editTagsInline(tagBtn); return; }
  const rm = e.target.closest("[data-rm]");
  if (rm) {
    const id = +rm.dataset.rm;
    const d = LIB.documents.find((x) => x.id === id);
    if (!confirm(`从库中移除「${d ? (d.title || d.name) : ""}」？\n磁盘文件不会删除，该文档的划线记录会被清除。`)) return;
    await libPost("/api/library/documents/remove", { id });
    loadLibrary();
    return;
  }
  const openBtn = e.target.closest("[data-open]");
  if (openBtn && !openBtn.disabled) openLibDoc(+openBtn.dataset.open);
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

