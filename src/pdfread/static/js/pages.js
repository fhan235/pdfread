/* ------------- 左栏：懒加载页面位图 ------------- */
// 按实际显示宽度与设备像素比计算所需 DPI, 高分屏不再模糊
function targetDpi(img, pageIdx) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = img.clientWidth || left.clientWidth || 600;
  const [pw] = INFO.sizes[pageIdx - 1] || [612];
  // 物理像素宽 / PDF 逻辑宽(1/72 英寸) = 所需 DPI
  const dpi = Math.round((cssW * dpr * 72) / pw);
  return Math.min(300, Math.max(120, dpi));
}

function buildLeft() {
  left.innerHTML = "";
  const epub = INFO.fmt === "epub";
  for (let i = 1; i <= INFO.pages; i++) {
    const [w, h] = INFO.sizes[i - 1] || [612, 792];
    const d = document.createElement("div");
    d.className = "pg";
    d.id = "L" + i;
    d.innerHTML = `<span class="pgno">${i} / ${INFO.pages}</span>`
      + (epub
        ? `<div class="echapter" data-p="${i}"><span class="loading">章节加载中…</span></div>`
        : `<div class="imgwrap"><img data-p="${i}" style="aspect-ratio:${w}/${h}"`
          + ` alt="第 ${i} 页" draggable="false" /><div class="hlayer"></div></div>`);
    left.appendChild(d);
  }
  attachLeftObserver();
  centerLeft();   // 持久化的放大倍率下, 打开文档即居中
  loadMarks();
}

/* 进入视口才真正请求内容，98 页也不卡; 标签恢复后也需要重新接管占位图 */
function attachLeftObserver() {
  if (leftObserver) leftObserver.disconnect();
  if (!INFO || !INFO.doc) return;
  const documentId = INFO.doc;
  const epub = INFO.fmt === "epub";
  const io = new IntersectionObserver((ents) => {
    ents.forEach((e) => {
      if (!e.isIntersecting) return;
      const el = e.target;
      const p = +el.dataset.p;
      if (epub) {
        if (el.dataset.done) return;
        el.dataset.done = "1";
        io.unobserve(el);
        fetch(`/api/chapter/${p}?doc=${documentId}`)
          .then((r) => r.ok ? r.text() : Promise.reject(r.status))
          .then((html) => {
            // 图片占位前缀替换为包内资源端点
            el.innerHTML = html.replaceAll("__EPUB_RES__",
              `/api/epub-res/${p}?doc=${documentId}&path=`);
          })
          .catch(() => { el.innerHTML = `<span class="loading">章节加载失败</span>`; });
        return;
      }
      if (el.src && el.dataset.dpi >= targetDpi(el, p) - 30) return;
      const dpi = targetDpi(el, p);
      el.dataset.dpi = dpi;
      el.src = `/api/page/${p}.png?dpi=${dpi}&doc=${documentId}`;
      io.unobserve(el);
    });
  }, { rootMargin: "700px" });
  leftObserver = io;
  left.querySelectorAll(epub ? ".echapter" : "img").forEach((i) => io.observe(i));
}

// 窗口明显变宽时(如全屏/分屏调整)按新宽度重取高清图(只绑定一次)
let lastW = 0, rszTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(rszTimer);
  rszTimer = setTimeout(() => {
    if (!leftObserver || !INFO || INFO.fmt === "epub") return;
    if (lastW && Math.abs(left.clientWidth - lastW) < lastW * 0.25) return;
    lastW = left.clientWidth;
    left.querySelectorAll(".pg img").forEach((i) => leftObserver.observe(i));
  }, 300);
}, { passive: true });

/* ---------------- 翻译（SSE 流式） ---------------- */
function run() {
  if (running) { es && es.close(); finish(); return; }
  if (polishing) { $("#runStatus").textContent = "润色进行中，请先停止或等待完成"; return; }
  const t0 = curTab();
  if (t0 && t0.view === "polished") {
    $("#runStatus").textContent = "当前是润色通读视图，点顶栏「逐页」返回后再翻译";
    return;
  }
  if (!INFO || !INFO.loaded) { openPicker(); return; }
  if (!INFO.ready) { openCfg(); return; }

  const s = Number($("#pStart").value), e = Number($("#pEnd").value);
  const lim = e === 0 ? INFO.pages : e;
  if (!Number.isInteger(s) || !Number.isInteger(e) || s < 1 || s > lim || lim > INFO.pages) {
    $("#runStatus").textContent = "请输入有效页码范围";
    return;
  }

  right.innerHTML = "";
  for (let i = s; i <= lim; i++) {
    const d = document.createElement("div");
    d.className = "pg"; d.id = "R" + i;
    d.innerHTML =
      `<span class="pgno">${i} / ${INFO.pages}</span>` +
      `<div class="skeleton">翻译中…</div>`;
    right.appendChild(d);
  }

  running = true;
  const tab = curTab();
  if (tab) { tab.running = true; renderTabs(); }
  $("#btnRun").textContent = "停止";
  let done = 0, total = lim - s + 1;

  es = new EventSource(`/api/translate?start=${s}&end=${e}&doc=${INFO.doc}`);
  const stream = es;

  es.addEventListener("page", (ev) => {
    const d = JSON.parse(ev.data);
    if (es !== stream || d.doc !== INFO.doc) return;
    renderPage(d.page, d.items);
    done++;
    $("#bar > div").style.width = (done / total * 100) + "%";
  });
  es.addEventListener("done", () => { if (es === stream) { stream.close(); finish(); } });
  es.addEventListener("failure", () => { if (es === stream) { stream.close(); finish(); } });
  es.onerror = () => { if (es === stream) { stream.close(); finish(); } };
}

function finish() {
  running = false;
  const tab = curTab();
  if (tab) { tab.running = false; renderTabs(); }
  $("#btnRun").textContent = "开始翻译";
  setTimeout(() => ($("#bar > div").style.width = "0"), 900);
}

/* ---------------- 页码跳转 ---------------- */
function jumpToPage() {
  if (!INFO || !INFO.loaded) { openPicker(); return; }
  const n = Number($("#jumpTo").value);
  if (!Number.isInteger(n) || n < 1 || n > INFO.pages) {
    $("#runStatus").textContent = `请输入 1–${INFO.pages} 之间的页码`;
    return;
  }
  // 用几何坐标计算面板内偏移, 不受页眉/标签栏高度影响
  const paneY = (pane, el) =>
    el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
  suppress.left = suppress.right = performance.now() + 400;
  lastTop.left = lastTop.right = n;
  const l = document.getElementById("L" + n);
  if (l) left.scrollTop = paneY(left, l) - 6;
  const r = document.getElementById("R" + n);
  if (r) {
    right.scrollTop = paneY(right, r) - 6;
    $("#runStatus").textContent = "";
  } else {
    $("#runStatus").textContent = `已跳到原文第 ${n} 页（该页译文尚未生成）`;
  }
  $("#jumpTo").value = "";
  $("#jumpTo").placeholder = n + " / " + INFO.pages;
}

/* ---------------- 整体润色(第二遍) ---------------- */
function togglePolish() {
  if (polishing) {  // 进行中再点 = 停止
    if (esP) { esP.close(); esP = null; }
    finishPolish("已停止，已完成页面保留在缓存中");
    return;
  }
  const t = curTab();
  if (!INFO || !INFO.loaded || !t) { openPicker(); return; }

  if (t.view === "polished") {  // 返回逐页对照视图
    t.polishedHTML = right.innerHTML;
    t.view = "pages";
    right.classList.remove("polish-mode");
    right.innerHTML = t.rightHTML
      || `<div class="empty">点击 <b>开始翻译</b> 默认翻译全文</div>`;
    $("#btnPolish").textContent = "润色";
    $("#runStatus").textContent = "";
    return;
  }

  // 已有润色结果 -> 直接切换, 不重复消耗
  if (t.polishedHTML != null) {
    t.rightHTML = right.innerHTML;
    t.view = "polished";
    right.classList.add("polish-mode");
    right.innerHTML = t.polishedHTML;
    right.scrollTop = 0;
    $("#btnPolish").textContent = "逐页";
    $("#runStatus").textContent = "";
    return;
  }
  startPolish(t);
}

function startPolish(t) {
  if (running) { $("#runStatus").textContent = "翻译进行中，请先停止"; return; }
  if (!INFO.ready) { openCfg(); return; }
  const chars = (INFO.chars / 10000).toFixed(1);
  if (!confirm(`将对全部已翻译内容（原文约 ${chars} 万字符）做第二遍整体润色，`
    + `消耗与全文翻译相当的额度，耗时也与全文翻译相当。\n`
    + `未翻译的页会跳过；原逐页译文保留不动。\n\n继续？`)) return;

  t.rightHTML = right.innerHTML;
  t.view = "polished";
  right.classList.add("polish-mode");
  $("#btnPolish").textContent = "停止润色";
  // 复用 R{页码} 骨架, renderPage / 跳转 / 导出 / 滚动同步全部沿用
  right.innerHTML = "";
  for (let i = 1; i <= INFO.pages; i++) {
    const d = document.createElement("div");
    d.className = "pg"; d.id = "R" + i;
    d.innerHTML = `<span class="pgno">${i} / ${INFO.pages}</span>`
      + `<div class="skeleton">等待润色…</div>`;
    right.appendChild(d);
  }
  right.scrollTop = 0;

  polishing = true;
  let done = 0, skipped = 0;
  esP = new EventSource(`/api/polish?doc=${INFO.doc}`);
  const stream = esP;
  esP.addEventListener("page", (ev) => {
    const d = JSON.parse(ev.data);
    if (esP !== stream || d.doc !== INFO.doc) return;
    if (d.skip) {
      skipped++;
      const box = document.getElementById("R" + d.page);
      if (box) box.innerHTML = `<span class="pgno">${d.page} / ${INFO.pages}</span>`
        + `<div class="skeleton" style="opacity:.4">（本页尚未翻译，已跳过）</div>`;
    } else {
      renderPage(d.page, d.items);
    }
    done++;
    $("#bar > div").style.width = (done / INFO.pages * 100) + "%";
  });
  esP.addEventListener("done", (ev) => {
    if (esP !== stream) return;
    const d = JSON.parse(ev.data);
    finishPolish(d.skipped ? `润色完成（${d.skipped} 页未翻译被跳过）` : "润色完成");
  });
  esP.addEventListener("failure", (ev) => {
    if (esP !== stream) return;
    const d = JSON.parse(ev.data);
    finishPolish(d.message || "润色中断");
  });
  esP.onerror = () => { if (esP === stream) finishPolish("连接中断"); };
}

function finishPolish(msg) {
  polishing = false;
  if (esP) { esP.close(); esP = null; }
  const t = curTab();
  if (t && t.view === "polished") {
    t.polishedHTML = right.innerHTML;  // 留下半成品, 下次直接续看
    $("#btnPolish").textContent = "逐页";
  } else {
    $("#btnPolish").textContent = "润色";
  }
  if (msg) $("#runStatus").textContent = msg;
  setTimeout(() => ($("#bar > div").style.width = "0"), 900);
}

function renderPage(n, items) {
  const box = document.getElementById("R" + n);
  if (!box) return;
  const head = `<span class="pgno">${n} / ${INFO.pages}</span>`;
  // 未翻译的块(按设置跳过的图内标签等)不占据译文版面
  const shown = items.filter((it) => it.dst);
  if (!shown.length) {
    box.innerHTML = head
      + `<div class="skeleton" style="opacity:.4">（本页无正文）</div>`;
    return;
  }
  box.innerHTML = head + shown.map((it) => {
    const bad = it.status === "failed" || it.dst.startsWith("[翻译失败]");
    const dst = tidy(it.dst);
    return `<div class="para ${it.kind}${bad ? " err" : ""}${showSrc ? " show-src" : ""}">`
      + `<div class="dst">${esc(dst)}</div>`
      + `<div class="src">${esc(it.src)}</div></div>`;
  }).join("");
}

/* 清理模型返回的多余换行/空白，避免译文出现大量断行 */
function tidy(s) {
  return s.replace(/\r/g, "")
          .replace(/\s*\n\s*/g, " ")
          .replace(/[ \t\u3000]{2,}/g, " ")
          .trim();
}

const esc = (s) => s.replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------- 滚动同步 ---------------- */
// 滚动同步防振荡: 记录每栏最近一次被"程序滚动"的时间,
// 该栏随后的回弹事件在抑制窗口内忽略; 配合滞回阈值,
// 阻断左右栏因页面高度不同、比例换算舍入造成的来回拉扯(自动漂移)
const suppress = { left: 0, right: 0 };
const lastTop = { left: 0, right: 0 };
function topPage(pane, prefix) {
  const y = pane.scrollTop;
  const kids = pane.children;
  for (let i = 0; i < kids.length; i++) {
    const k = kids[i];
    if (k.offsetTop + k.offsetHeight > y + 40) {
      return { n: +k.id.slice(1), frac: (y - k.offsetTop) / (k.offsetHeight || 1) };
    }
  }
  return null;
}
function sync(from, to) {
  if (!syncOn) return;
  const fk = from === left ? "left" : "right";
  const tk = to === left ? "left" : "right";
  // 源栏刚被程序滚动过(它是上一次同步的目标), 忽略其回弹事件
  if (performance.now() < suppress[fk]) return;
  const cur = topPage(from);
  if (!cur) return;
  const tgt = to.querySelector("#" + (to === left ? "L" : "R") + cur.n);
  if (!tgt) return;
  const y = tgt.offsetTop + Math.max(0, cur.frac) * tgt.offsetHeight;
  const pageChanged = cur.n !== lastTop[fk];
  lastTop[fk] = cur.n;
  // 页码没变且偏差很小 -> 不动, 这是阻断振荡的关键滞回
  if (!pageChanged && Math.abs(to.scrollTop - y) < 28) return;
  if (Math.abs(to.scrollTop - y) < 4) return;
  suppress[tk] = performance.now() + 250;
  to.scrollTop = y;
}
left.addEventListener("scroll", () => sync(left, right), { passive: true });
right.addEventListener("scroll", () => sync(right, left), { passive: true });

