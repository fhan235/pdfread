/* ---------------- 划线高亮 ---------------- */
/* 坐标以 PDF 点存储, 前端按页面尺寸换算成百分比定位,
   缩放 / 高清重取图都不会错位 */
const marksByDoc = {};   // 运行时文档 id -> { 页码: [{id, rects, color}] }
const wordsByDoc = {};   // 运行时文档 id -> { 页码: [[x0,y0,x1,y1,词,块,行]] }
let drag = null;

async function loadMarks() {
  if (!INFO || !INFO.doc || !INFO.lib || INFO.fmt !== "pdf") return;
  if (!marksByDoc[INFO.doc]) {
    try {
      const r = await fetch("/api/highlights?doc=" + INFO.doc);
      if (!r.ok) return;
      const d = await r.json();
      const byPage = {};
      for (const m of d.items) (byPage[m.page] = byPage[m.page] || []).push(m);
      marksByDoc[INFO.doc] = byPage;
    } catch (e) { return; }
  }
  renderAllMarks();
}

function renderAllMarks() {
  if (!INFO || !INFO.doc) return;
  for (const p of Object.keys(marksByDoc[INFO.doc] || {})) renderMarks(+p);
}

function renderMarks(page) {
  const layer = document.querySelector("#L" + page + " .hlayer");
  if (!layer || !INFO || !INFO.sizes) return;
  layer.querySelectorAll(".hl").forEach((el) => el.remove());
  const [pw, ph] = INFO.sizes[page - 1] || [612, 792];
  for (const m of (marksByDoc[INFO.doc] || {})[page] || []) {
    for (const r of m.rects) layer.appendChild(hlDiv(r, pw, ph, m.id, ""));
  }
}

function hlDiv(r, pw, ph, id, cls) {
  const el = document.createElement("div");
  el.className = "hl" + cls;
  if (id) el.dataset.hid = id;
  el.style.left = (r[0] / pw * 100) + "%";
  el.style.top = (r[1] / ph * 100) + "%";
  el.style.width = ((r[2] - r[0]) / pw * 100) + "%";
  el.style.height = ((r[3] - r[1]) / ph * 100) + "%";
  return el;
}

async function ensureWords(page) {
  const bag = (wordsByDoc[INFO.doc] = wordsByDoc[INFO.doc] || {});
  if (bag[page]) return bag[page];
  const r = await fetch(`/api/words/${page}?doc=` + INFO.doc);
  if (!r.ok) throw new Error("词坐标读取失败");
  const d = await r.json();
  bag[page] = d.words;
  return d.words;
}

/* 命中词: 优先包含点的词, 否则取距离最近的词(限制阈值, 防误触) */
function hitWord(words, x, y) {
  let best = -1, bestD = 1e9;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (x >= w[0] && x <= w[2] && y >= w[1] - 2 && y <= w[3] + 2) return i;
    const cy = (w[1] + w[3]) / 2;
    const dx = x < w[0] ? w[0] - x : (x > w[2] ? x - w[2] : 0);
    const dist = Math.abs(y - cy) * 10 + dx;
    if (dist < bestD) { bestD = dist; best = i; }
  }
  return bestD < 30 ? best : -1;
}

/* 起止词之间按 (块, 行) 分组合并成每行一个矩形 */
function selectionRects(words, i, j) {
  const lo = Math.min(i, j), hi = Math.max(i, j);
  const lines = new Map();
  const order = [];
  for (let k = lo; k <= hi; k++) {
    const w = words[k];
    const key = w[5] * 4096 + w[6];
    if (!lines.has(key)) {
      lines.set(key, [w[0], w[1], w[2], w[3]]);
      order.push(key);
    } else {
      const r = lines.get(key);
      r[0] = Math.min(r[0], w[0]); r[1] = Math.min(r[1], w[1]);
      r[2] = Math.max(r[2], w[2]); r[3] = Math.max(r[3], w[3]);
    }
  }
  return order.map((k) => lines.get(k));
}

/* 选区原文(按词拼接), 供划词翻译 */
function selectionText(words, i, j) {
  const lo = Math.min(i, j), hi = Math.max(i, j);
  return words.slice(lo, hi + 1).map((w) => w[4]).join(" ").slice(0, 20000);
}

function pagePoint(wrap, page, e) {
  const [pw, ph] = INFO.sizes[page - 1] || [612, 792];
  const rect = wrap.getBoundingClientRect();
  return {
    x: Math.min(Math.max((e.clientX - rect.left) / (rect.width || 1) * pw, 0), pw),
    y: Math.min(Math.max((e.clientY - rect.top) / (rect.height || 1) * ph, 0), ph),
  };
}

/* 拖选后先弹「确认划线」浮动按钮(ReadPaper 式), 确认才入库;
   点其他位置 / 开始新拖选 / 滚动都会取消待确认的预览 */
let pendingMark = null;   // {page, rects, layer}

function discardPending() {
  if (pendingMark) {
    pendingMark.layer.querySelectorAll(".hl.preview").forEach((el) => el.remove());
    pendingMark = null;
  }
  $("#hlConfirm").style.display = "none";
}

function previewSelection() {
  const { layer, words, start, cur, page } = drag;
  layer.querySelectorAll(".hl.preview").forEach((el) => el.remove());
  const [pw, ph] = INFO.sizes[page - 1] || [612, 792];
  for (const r of selectionRects(words, start, cur)) {
    layer.appendChild(hlDiv(r, pw, ph, 0, " preview"));
  }
}

/* 文库登记偶发失败会让 INFO.lib 缺失(后端静默容错), 导致划线整体失效。
   按下时若缺 lib 则重新拉取一次 info 尝试恢复, 并给出可见提示 */
let libRecovering = false;
async function recoverLib() {
  if (libRecovering || !INFO || !INFO.doc) return;
  libRecovering = true;
  try {
    const r = await fetch("/api/info?doc=" + encodeURIComponent(INFO.doc));
    if (!r.ok) return;
    const d = await r.json();
    if (d.doc !== INFO.doc) return;
    INFO = d;
    const t = curTab();
    if (t) t.info = d;
    $("#runStatus").textContent = INFO.lib
      ? ""
      : "该文档未登记到文库，划线与阅读进度暂不可用，可尝试重新打开文档";
  } catch (e) { /* 网络失败保持现状 */ }
  libRecovering = false;
}

left.addEventListener("mousedown", (e) => {
  if (e.button !== 0 || !INFO || INFO.fmt !== "pdf") return;
  if (!INFO.lib) { recoverLib(); return; }
  const wrap = e.target.closest(".imgwrap");
  if (!wrap) return;
  const pg = wrap.closest(".pg");
  if (!pg) return;
  const page = +pg.id.slice(1);
  e.preventDefault();   // 阻止图片拖拽/默认选区
  hideHlPop();
  discardPending();
  ensureWords(page).then((words) => {
    const pt = pagePoint(wrap, page, e);
    drag = { page, wrap, layer: wrap.querySelector(".hlayer"), words,
             start: hitWord(words, pt.x, pt.y), cur: -1,
             moved: false, sx: e.clientX, sy: e.clientY };
    drag.cur = drag.start;
  }).catch(() => {});
});

document.addEventListener("mousemove", (e) => {
  if (!drag) return;
  if (!(e.buttons & 1)) { drag = null; return; }  // 词表异步加载期间已松手
  if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 4) {
    drag.moved = true;
  }
  if (!drag.moved) return;
  const pt = pagePoint(drag.wrap, drag.page, e);
  const idx = hitWord(drag.words, pt.x, pt.y);
  if (idx < 0) return;
  if (drag.start < 0) {
    // 按下点落在行距/空白处: 拖到第一个词时才确立起点, 不作废整段手势
    drag.start = idx;
    drag.cur = idx;
  } else if (idx !== drag.cur) {
    drag.cur = idx;
  }
  previewSelection();
});

/* 保留预览并弹出「划线/翻译」确认按钮 */
function confirmSelection(page, layer, rects, text, x, y) {
  pendingMark = { page, rects, layer, text, x, y };
  const pop = $("#hlConfirm");
  pop.style.display = "flex";   // 两按钮并排
  pop.style.left = Math.min(x, window.innerWidth - 220) + "px";
  pop.style.top = (y + 8) + "px";
}

document.addEventListener("mouseup", (e) => {
  if (!drag) return;
  const d = drag;
  drag = null;
  discardPending();
  const valid = d.moved && d.start >= 0 && d.cur >= 0 && INFO && INFO.doc;
  const rects = valid ? selectionRects(d.words, d.start, d.cur) : [];
  if (!rects.length) {   // 单击或无效选区: 清掉预览, 不弹确认
    d.layer.querySelectorAll(".hl.preview").forEach((el) => el.remove());
    return;
  }
  // 保留预览, 等用户点浮动按钮确认
  confirmSelection(d.page, d.layer, rects,
                   selectionText(d.words, d.start, d.cur), e.clientX, e.clientY);
});

/* 双击单词: 直接选中该词, 弹出划线/翻译确认 */
left.addEventListener("dblclick", (e) => {
  if (!INFO || INFO.fmt !== "pdf") return;
  if (!INFO.lib) { recoverLib(); return; }
  const wrap = e.target.closest(".imgwrap");
  if (!wrap) return;
  const pg = wrap.closest(".pg");
  if (!pg) return;
  const page = +pg.id.slice(1);
  ensureWords(page).then((words) => {
    const pt = pagePoint(wrap, page, e);
    const idx = hitWord(words, pt.x, pt.y);
    if (idx < 0) return;
    discardPending();
    const layer = wrap.querySelector(".hlayer");
    const rects = selectionRects(words, idx, idx);
    const [pw, ph] = INFO.sizes[page - 1] || [612, 792];
    for (const r of rects) layer.appendChild(hlDiv(r, pw, ph, 0, " preview"));
    confirmSelection(page, layer, rects,
                     selectionText(words, idx, idx), e.clientX, e.clientY);
  }).catch(() => {});
});

$("#hlOk").onclick = async () => {
  const p = pendingMark;
  pendingMark = null;
  $("#hlConfirm").style.display = "none";
  if (!p || !INFO || !INFO.doc) return;
  try {
    const r = await libPost("/api/highlights", { doc: INFO.doc, page: p.page, rects: p.rects });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const m = await r.json();
    const bag = (marksByDoc[INFO.doc] = marksByDoc[INFO.doc] || {});
    (bag[p.page] = bag[p.page] || []).push(m);
    renderMarks(p.page);   // 永久高亮取代预览
  } catch (err) {
    p.layer.querySelectorAll(".hl.preview").forEach((el) => el.remove());
    $("#runStatus").textContent = "划线保存失败：" + err;
  }
};

/* 点击已有划线 -> 删除浮层 */
left.addEventListener("click", (e) => {
  const hl = e.target.closest(".hl");
  if (!hl || !hl.dataset.hid) return;
  const pop = $("#hlPop");
  pop.dataset.hid = hl.dataset.hid;
  pop.style.display = "block";
  pop.style.left = Math.min(e.clientX, window.innerWidth - 130) + "px";
  pop.style.top = (e.clientY + 8) + "px";
});

function hideHlPop() { $("#hlPop").style.display = "none"; }

/* 划词翻译气泡 */
function showTrPop(text, x, y) {
  const pop = $("#trPop");
  pop.textContent = text;
  pop.style.display = "block";
  pop.style.left = Math.max(8, Math.min(x, window.innerWidth - 380)) + "px";
  pop.style.top = (y + 8) + "px";
}
function hideTrPop() { $("#trPop").style.display = "none"; }

$("#hlTr").onclick = async () => {
  const p = pendingMark;
  if (!p || !p.text || !INFO || !INFO.doc) return;
  $("#hlConfirm").style.display = "none";
  showTrPop("翻译中…", p.x, p.y);
  try {
    const r = await libPost("/api/translate-text", { doc: INFO.doc, text: p.text });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    showTrPop(d.dst, p.x, p.y);
  } catch (err) {
    showTrPop("翻译失败：" + err, p.x, p.y);
  }
};

$("#hlDel").onclick = async () => {
  const id = +$("#hlPop").dataset.hid;
  hideHlPop();
  if (!INFO || !INFO.doc) return;
  await libPost("/api/highlights/delete", { id });
  const bag = marksByDoc[INFO.doc] || {};
  for (const p of Object.keys(bag)) {
    const before = bag[p].length;
    bag[p] = bag[p].filter((m) => m.id !== id);
    if (bag[p].length !== before) renderMarks(+p);
  }
};
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#hlPop")) hideHlPop();
  if (!e.target.closest("#hlConfirm")) discardPending();
  if (!e.target.closest("#trPop")) hideTrPop();
}, true);
left.addEventListener("scroll", () => { hideHlPop(); discardPending(); },
                      { passive: true });

