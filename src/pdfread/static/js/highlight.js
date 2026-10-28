/* ---------------- 划线高亮 ---------------- */
/* 坐标以 PDF 点存储, 前端按页面尺寸换算成百分比定位,
   缩放 / 高清重取图都不会错位 */
const marksByDoc = {};   // 运行时文档 id -> { 页码: [{id, rects, color}] }
const wordsByDoc = {};   // 运行时文档 id -> { 页码: [[x0,y0,x1,y1,词,块,行]] }
let drag = null;

async function loadMarks() {
  if (!INFO || !INFO.doc || !INFO.lib || INFO.fmt === "epub") return;
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

left.addEventListener("mousedown", (e) => {
  if (e.button !== 0 || !INFO || !INFO.lib || INFO.fmt === "epub") return;
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
  if (!drag.moved || drag.start < 0) return;
  const pt = pagePoint(drag.wrap, drag.page, e);
  const idx = hitWord(drag.words, pt.x, pt.y);
  if (idx >= 0 && idx !== drag.cur) { drag.cur = idx; }
  previewSelection();
});

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
  pendingMark = { page: d.page, rects, layer: d.layer };
  const pop = $("#hlConfirm");
  pop.style.display = "block";
  pop.style.left = Math.min(e.clientX, window.innerWidth - 120) + "px";
  pop.style.top = (e.clientY + 8) + "px";
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
}, true);
left.addEventListener("scroll", () => { hideHlPop(); discardPending(); },
                      { passive: true });

