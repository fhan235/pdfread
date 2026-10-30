/* ---------------- 多文档标签 ---------------- */
/* 每个标签保存一份面板快照(HTML+滚动位置+页码范围),
   切换时恢复; 未激活标签的翻译流会停掉, 已翻段落留在服务端缓存,
   回来后重新翻译可秒出已缓存部分 */
let tabs = [];      // {id, info, leftHTML, rightHTML, leftTop, rightTop, pStart, pEnd, running}
let activeTab = null;

const curTab = () => tabs.find((t) => t.id === activeTab);
const tabName = (t) => (t.info && (t.info.title || t.info.name)) || "未命名";

function renderTabs() {
  const bar = $("#tabbar");
  bar.classList.toggle("on", tabs.length > 0);
  bar.innerHTML = tabs.map((t) =>
    `<div class="tab${t.id === activeTab ? " on" : ""}" data-tab="${t.id}"`
    + ` title="${esc(tabName(t))}">`
    + (t.running ? `<span class="dot">●</span>` : "")
    + `<span class="nm">${esc(tabName(t))}</span>`
    + `<button class="x" data-x="${t.id}" title="关闭该文档">×</button></div>`
  ).join("") + `<button id="tabAdd" class="ghost" title="打开新文件">＋</button>`;
  $("#tabAdd").onclick = openPicker;
}

$("#tabbar").addEventListener("click", (e) => {
  const x = e.target.closest(".x");
  if (x) { e.stopPropagation(); closeTab(x.dataset.x); return; }
  const tab = e.target.closest(".tab");
  if (tab) switchTab(tab.dataset.tab);
});

function snapshotTab() {
  discardPending();   // 切换/关闭标签前取消未确认的划线预览
  const t = curTab();
  if (!t) return;
  // 右栏内容按当前视图分别存档: 逐页译文 / 润色全文互不覆盖
  if (t.view === "polished") t.polishedHTML = right.innerHTML;
  else t.rightHTML = right.innerHTML;
  Object.assign(t, {
    info: INFO,
    leftHTML: left.innerHTML,
    leftTop: left.scrollTop, rightTop: right.scrollTop,
    pStart: $("#pStart").value, pEnd: $("#pEnd").value,
    qaHist: qaHist,
  });
}

function restoreTab(t) {
  INFO = t.info;
  activeTab = t.id;
  sessionStorage.setItem("pdfread.doc", t.id);
  $("#docTitle").textContent = INFO.title || INFO.name;
  refreshMeta();
  $("#btnRun").title = INFO.ready ? "" : INFO.warn;
  if (t.leftHTML == null) {
    // 新标签: 首次构建
    buildLeft();
    // 恢复上次阅读进度(静默, 不写状态栏)
    if (INFO.progress > 1) {
      setTimeout(() => scrollToPage(INFO.progress, true), 150);
    }
    if (!INFO.ready) {
      right.innerHTML = `<div class="empty">尚未配置翻译服务密钥<br /><br />`
        + `<span style="font-size:12px">正在打开设置面板…</span></div>`;
      // 首次打开自动弹出设置, 会话内只弹一次, 避免反复打扰
      if (!sessionStorage.getItem("cfgPrompted")) {
        sessionStorage.setItem("cfgPrompted", "1");
        setTimeout(openCfg, 350);
      }
    } else {
      right.innerHTML = `<div class="empty">点击 <b>开始翻译</b> 默认翻译全文`
        + `<br /><br /><span style="font-size:12px">顶栏「跳到」输入页码可一键跳转</span></div>`;
    }
  } else {
    left.innerHTML = t.leftHTML;
    right.innerHTML = (t.view === "polished" && t.polishedHTML != null)
      ? t.polishedHTML : (t.rightHTML ?? "");
    left.scrollTop = t.leftTop || 0;
    right.scrollTop = t.rightTop || 0;
    attachLeftObserver();  // 恢复后重新接管未加载的占位图
  }
  right.classList.toggle("polish-mode", t.view === "polished");
  $("#btnPolish").textContent = t.view === "polished" ? "逐页" : "润色";
  qaHist = t.qaHist || [];
  renderQA();
  renderTOC();
  lastProg = (INFO && INFO.progress) || 0;
  $("#pStart").value = t.pStart ?? 1;
  $("#pEnd").value = t.pEnd ?? 0;
  suppress.left = suppress.right = 0;
  lastTop.left = lastTop.right = 0;
}

function switchTab(id) {
  if (id === activeTab || !tabs.some((t) => t.id === id)) return;
  const wasRunning = running || polishing;
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false;
  polishing = false;
  const old = curTab();
  if (old) old.running = false;
  $("#btnRun").textContent = "开始翻译";
  snapshotTab();
  restoreTab(tabs.find((t) => t.id === id));
  $("#runStatus").textContent = wasRunning
    ? "已切换文档，任务已停止（已完成部分在缓存中，重新执行可秒出）" : "";
  renderTabs();
}

async function closeTab(id) {
  const idx = tabs.findIndex((t) => t.id === id);
  if (idx < 0) return;
  if (id === activeTab && es) { es.close(); es = null; }
  if (id === activeTab && esP) { esP.close(); esP = null; polishing = false; }
  fetch("/api/docs/close", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).catch(() => {});
  tabs.splice(idx, 1);
  if (activeTab === id) {
    activeTab = null;
    running = false;
    $("#btnRun").textContent = "开始翻译";
    if (tabs.length) {
      restoreTab(tabs[Math.min(idx, tabs.length - 1)]);
    } else {
      INFO = null;
      sessionStorage.removeItem("pdfread.doc");
      syncEreadBtn();
      $("#docTitle").textContent = "未加载 PDF";
      $("#docMeta").textContent = "点击「打开」选择文件，或直接拖入 PDF";
      left.innerHTML = "";
      right.innerHTML = `<div class="empty">点击「打开」选择文件，或直接拖入 PDF</div>`;
    }
  }
  renderTabs();
}

/* 打开成功后的统一入口: 已有同 id 标签则重建, 否则新建标签 */
function addOrActivateTab(data) {
  if (document.body.classList.contains("lib")) loadLibrary();  // 侧栏打开时即时反映新入库
  if (es) { es.close(); es = null; }
  if (esP) { esP.close(); esP = null; }
  running = false;
  polishing = false;
  $("#btnRun").textContent = "开始翻译";
  let t = tabs.find((x) => x.id === data.doc);
  if (t) {
    Object.assign(t, { info: data, leftHTML: null, rightHTML: null,
                       polishedHTML: null, view: "pages",
                       pStart: 1, pEnd: 0, running: false });
  } else {
    t = { id: data.doc, info: data, leftHTML: null, rightHTML: null,
          polishedHTML: null, view: "pages",
          pStart: 1, pEnd: 0, running: false };
    tabs.push(t);
  }
  restoreTab(t);
  renderTabs();
}

/* ---------------- 初始化 ---------------- */
async function init(data = null) {
  if (data) { addOrActivateTab(data); return; }
  // 启动/刷新: 恢复服务端仍打开的全部文档
  let list = { docs: [] };
  try { list = await (await fetch("/api/docs")).json(); } catch (e) {}
  const saved = sessionStorage.getItem("pdfread.doc") || "";
  tabs = [];
  for (const d of list.docs) {
    try {
      const r = await fetch("/api/info?doc=" + encodeURIComponent(d.id));
      if (!r.ok) continue;  // 已过期(如被并发关闭)则跳过
      const infoData = await r.json();
      tabs.push({ id: d.id, info: infoData, leftHTML: null, rightHTML: null,
                  polishedHTML: null, view: "pages",
                  pStart: 1, pEnd: 0, running: false });
    } catch (e) { /* 单个恢复失败不影响其余 */ }
  }
  if (!tabs.length) {
    activeTab = null;
    $("#docTitle").textContent = "未加载 PDF";
    $("#docMeta").textContent = "在文库中导入或打开文档";
    left.innerHTML = "";
    renderTabs();
    showLibrary();
    return;
  }
  restoreTab(tabs.find((t) => t.id === saved) || tabs[tabs.length - 1]);
  renderTabs();
}

