/* ---------------- 文内链接: 点击引用跳转 + 返回 ---------------- */
/* 左栏是 SVG 页面图, PDF 自带的链接不可点。按页向后端取内部链接
   (引用 [12]、图表/章节交叉引用), 在 .llayer 上叠透明热区。
   坐标均为 PDF 点(显示坐标), 按 INFO.sizes 换算成百分比, 缩放不错位。
   跳转前把两栏的阅读位置压入返回栈(按文档分开), 「返回」/ Alt+← /
   鼠标侧键回到原处。位置记为 (页码, 页内比例), 中途缩放也能准确回位 */
const navBack = {};           // 运行时文档 id -> [{left, right}]
const linksLoading = new Set(); // "文档:页码", 防重复请求
const NAV_MAX = 50;

/* 热区 HTML(纯函数, 便于测试); 数值来自后端, 仍强制转数字。
   ty 是锚点(高亮落点), tv 是滚动位置: 指向图片时为图的顶部
   (图注在图下方, 直接滚到锚点会把图挤出视野), 其余情况与 ty 相同 */
function linkHTML(links, pw, ph) {
  const pct = (v, base) => Math.min(Math.max(+v, 0), base) / base * 100;
  return links.map(([x0, y0, x1, y1, tp, ty, tv]) => {
    const n = Math.trunc(+tp), y = +ty;
    const v = tv != null && Number.isFinite(+tv) ? +tv : y;   // 缺省(旧格式)时同锚点
    const l = pct(x0, pw), t = pct(y0, ph), w = pct(x1, pw) - l, h = pct(y1, ph) - t;
    if (!(n >= 1) || !Number.isFinite(y) || !(w > 0) || !(h > 0)) return "";
    return `<div class="plink" data-tp="${n}" data-ty="${y}" data-tv="${v}"`
      + ` title="跳转到第 ${n} 页"`
      + ` style="left:${l.toFixed(3)}%;top:${t.toFixed(3)}%;`
      + `width:${w.toFixed(3)}%;height:${h.toFixed(3)}%"></div>`;
  }).join("");
}

/* 取一页链接并渲染。完成时按页码重新查找当前 DOM 中的图层:
   请求期间若切换过标签, 旧元素已脱离文档, 结果直接丢弃,
   回到该标签后 observer 会因图层未标记 done 而重新请求 */
async function loadLinks(page, doc) {
  const key = doc + ":" + page;
  const layer = document.querySelector(`#L${page} .llayer`);
  if (!layer || layer.dataset.done || linksLoading.has(key)) return;
  linksLoading.add(key);
  try {
    const r = await fetch(`/api/links/${page}?doc=${doc}`);
    if (!r.ok) return;
    const d = await r.json();
    if (!INFO || INFO.doc !== doc || !INFO.sizes) return;
    const cur = document.querySelector(`#L${page} .llayer`);
    if (!cur) return;
    const [pw, ph] = INFO.sizes[page - 1] || [612, 792];
    // 只替换热区, 不整体覆盖图层: 跳转常常正是让这一页进入视口、
    // 触发本次加载的, 此时图层里已有刚插入的落点高亮
    cur.querySelectorAll(".plink").forEach((el) => el.remove());
    cur.insertAdjacentHTML("afterbegin", linkHTML(d.links || [], pw, ph));
    cur.dataset.done = "1";
  } catch (e) { /* 失败允许下次滚入视口时重试 */ }
  finally { linksLoading.delete(key); }
}

/* 面板当前阅读位置: 视口顶部所在页及页内比例(几何计算, 不依赖 offsetParent) */
function panePos(pane, prefix) {
  const top = pane.getBoundingClientRect().top;
  for (const k of pane.children) {
    if (!k.id || k.id[0] !== prefix) continue;
    const r = k.getBoundingClientRect();
    if (r.bottom > top + 1) {
      return { n: +k.id.slice(1), frac: (top - r.top) / (r.height || 1) };
    }
  }
  return null;
}

function scrollPaneTo(pane, el, frac, margin) {
  const delta = el.getBoundingClientRect().top - pane.getBoundingClientRect().top;
  pane.scrollTop = Math.max(0, pane.scrollTop + delta + frac * el.offsetHeight - margin);
}

/* 程序滚动期间屏蔽滚动同步的回弹, 与 scrollToPage 一致 */
function holdSync(n) {
  suppress.left = suppress.right = performance.now() + 400;
  lastTop.left = lastTop.right = n;
}

function jumpToLink(n, y, view = y) {
  if (!INFO || !INFO.doc || INFO.fmt !== "pdf") return;
  const pg = document.getElementById("L" + n);
  const wrap = pg && pg.querySelector(".imgwrap");
  if (!wrap) return;
  const stack = (navBack[INFO.doc] = navBack[INFO.doc] || []);
  stack.push({ left: panePos(left, "L"), right: panePos(right, "R") });
  if (stack.length > NAV_MAX) stack.shift();

  const [, ph] = INFO.sizes[n - 1] || [612, 792];
  const clamp = (v) => Math.min(Math.max(v / ph, 0), 1);
  const frac = clamp(y), vfrac = clamp(Math.min(view, y));
  holdSync(n);
  // 相对页面图定位; 目标上方留一点余量, 避免条目首行/图顶紧贴栏顶
  scrollPaneTo(left, wrap, vfrac, 48);
  const r = document.getElementById("R" + n);
  if (r) scrollPaneTo(right, r, vfrac, 48);   // 译文按页内比例近似对齐
  flashTarget(n, frac);   // 高亮的始终是锚点(参考文献条目 / 图注那一行)
  syncLinkBack();
}

/* 落点短暂高亮, 让视线知道跳到了哪一行 */
function flashTarget(n, frac) {
  const layer = document.querySelector(`#L${n} .llayer`);
  if (!layer) return;
  layer.querySelectorAll(".ltarget").forEach((el) => el.remove());
  const mark = document.createElement("div");
  mark.className = "ltarget";
  mark.style.top = (frac * 100).toFixed(3) + "%";
  layer.appendChild(mark);
  setTimeout(() => mark.remove(), 1800);
}

function navBackGo() {
  if (!INFO || !INFO.doc) return;
  const stack = navBack[INFO.doc];
  const pos = stack && stack.pop();
  if (!pos) return;
  holdSync(pos.left ? pos.left.n : 0);
  const l = pos.left && document.getElementById("L" + pos.left.n);
  if (l) scrollPaneTo(left, l, pos.left.frac, 0);
  const r = pos.right && document.getElementById("R" + pos.right.n);
  if (r) scrollPaneTo(right, r, pos.right.frac, 0);
  syncLinkBack();
}

/* 「返回」按钮: 浮在左栏左下角; 栈空或左栏被隐藏时不显示 */
function syncLinkBack() {
  const btn = $("#linkBack");
  if (!btn) return;
  const n = (INFO && INFO.doc && navBack[INFO.doc] || []).length;
  const box = left.getBoundingClientRect ? left.getBoundingClientRect() : null;
  if (!n || !box || box.width < 40 || box.height < 60) {
    btn.style.display = "none";
    return;
  }
  btn.textContent = n > 1 ? `← 返回（${n}）` : "← 返回";
  btn.style.display = "block";
  btn.style.left = (box.left + 14) + "px";
  btn.style.top = (box.bottom - 50) + "px";
}

/* 文档重建(首次打开/重新解析)时清空该文档的返回栈 */
function resetLinkNav(doc) {
  if (doc) delete navBack[doc];
  syncLinkBack();
}

/* 区分点击与拖选: 按下后移动超过 4px 视为划线手势, 不触发跳转 */
let linkDown = null;
left.addEventListener("mousedown", (e) => {
  linkDown = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
}, true);

left.addEventListener("click", (e) => {
  // 只认应用自己生成的 PDF 链接层: EPUB/TXT 章节内容与应用同 DOM
  const a = e.target.closest(".plink");
  if (!a || !a.closest(".imgwrap .llayer") || !INFO || INFO.fmt !== "pdf") return;
  if (linkDown && Math.abs(e.clientX - linkDown.x) + Math.abs(e.clientY - linkDown.y) > 4) return;
  const n = Math.trunc(+a.dataset.tp), y = +a.dataset.ty, v = +a.dataset.tv;
  if (!(n >= 1 && n <= INFO.pages) || !Number.isFinite(y)) return;
  e.stopPropagation();
  jumpToLink(n, y, Number.isFinite(v) ? v : y);
});

/* 鼠标侧键「后退」 */
left.addEventListener("mouseup", (e) => {
  if (e.button === 3) { e.preventDefault(); navBackGo(); }
});

document.addEventListener("keydown", (e) => {
  if (!e.altKey || e.key !== "ArrowLeft") return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (INFO && INFO.doc && (navBack[INFO.doc] || []).length) {
    e.preventDefault();
    navBackGo();
  }
});

$("#linkBack").onclick = navBackGo;
window.addEventListener("resize", syncLinkBack);
// 分割条拖动、侧栏/目录开合都会改变左栏尺寸, 按钮随之重新定位
if (typeof ResizeObserver !== "undefined") new ResizeObserver(syncLinkBack).observe(left);
