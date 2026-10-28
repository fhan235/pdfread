/* ---------------- 阅读偏好(存 localStorage) ---------------- */
const PREF_KEY = "pdfread.prefs";
let prefs = { theme: "dark", font: "15", layout: "side", layoutPrev: "side",
              expFmt: "txt", expSrc: "dst", italicSrc: false, lib: false,
              zoom: 1, qa: false, leftPct: 0, libW: 320, qaW: 340,
              toc: false };

function loadPrefs() {
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); }
  catch (e) { /* 忽略损坏的偏好 */ }
}
function savePrefs() {
  localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
}
const THEME_ORDER = ["dark", "light", "paper", "green"];
const THEME_NAMES = { dark: "深色", light: "浅色", paper: "纸张", green: "护眼" };

function applyPrefs() {
  // paper / green 属于浅色体系: 复用 body.light 的组件适配, 只换配色变量
  document.body.classList.toggle("light", prefs.theme !== "dark");
  document.body.classList.toggle("theme-paper", prefs.theme === "paper");
  document.body.classList.toggle("theme-green", prefs.theme === "green");
  document.body.classList.toggle("italic-src", !!prefs.italicSrc);
  document.documentElement.style.setProperty("--rfs", prefs.font + "px");
  const m = document.querySelector("main");
  m.classList.toggle("stacked", prefs.layout === "stack");
  m.classList.toggle("tronly", prefs.layout === "tronly");
  m.classList.toggle("pdfonly", prefs.layout === "pdfonly");
  const bp = $("#btnPane");
  if (bp) {
    bp.textContent = prefs.layout === "pdfonly" ? "译文栏: 关" : "译文栏: 开";
    bp.classList.toggle("on", prefs.layout === "pdfonly");
  }
  const tb = $("#btnTheme");
  if (tb) tb.textContent = THEME_NAMES[prefs.theme] || "深色";
  const ci = $("#cbItalic");
  if (ci) ci.checked = !!prefs.italicSrc;
  const fr = $("#fontRange");
  if (fr) fr.value = prefs.font;
  const fv = $("#fontVal");
  if (fv) fv.textContent = prefs.font;
  const lw = $("#left");
  if (lw) lw.style.setProperty("--zoom", prefs.zoom || 1);
  const zv = $("#zoomVal");
  if (zv) zv.textContent = Math.round((prefs.zoom || 1) * 100) + "%";
  syncSeg("segTheme", prefs.theme);
  syncSeg("segFont", prefs.font);
  syncSeg("segLayout", prefs.layout);
  syncSeg("segExpFmt", prefs.expFmt);
  syncSeg("segExpSrc", prefs.expSrc);
  if (prefs.lib) showLibrary();
  document.body.classList.toggle("qa", !!prefs.qa);
  const bq = $("#btnQA");
  if (bq) bq.classList.toggle("on", !!prefs.qa);
  document.body.classList.toggle("toc", !!prefs.toc);
  const bt = $("#btnToc");
  if (bt) bt.classList.toggle("on", !!prefs.toc);
  applySplit();
}
function syncSeg(id, v) {
  document.querySelectorAll("#" + id + " button").forEach((b) =>
    b.classList.toggle("on", b.dataset.v === String(v)));
}
function segInit(id, key) {
  document.querySelectorAll("#" + id + " button").forEach((b) => {
    b.onclick = () => {
      prefs[key] = b.dataset.v;
      savePrefs();
      applyPrefs();
    };
  });
}

/* ---------------- 导出 ---------------- */
function doExport() {
  const items = right.querySelectorAll(".pg .para");
  const hint = $("#expHint");
  if (!items.length) {
    hint.textContent = "还没有可导出的译文，请先翻译";
    hint.className = "hint bad";
    if (!$("#cfg").classList.contains("on")) openCfg();
    return;
  }

  const bi = prefs.expSrc === "bi";
  const md = prefs.expFmt === "md";
  let count = 0;
  const out = [];
  const failed = right.querySelectorAll(".para.err").length;
  const incomplete = [...right.querySelectorAll(".skeleton")].filter((el) => !el.textContent.includes("无正文")).length;
  if (failed || incomplete) out.push(`注意：此导出不完整，已跳过 ${failed} 个失败段落，另有 ${incomplete} 页未完成。\n`);
  const title = (INFO?.title || INFO?.name || "pdfread")
    .replace(/[\\/:*?"<>|]/g, "_").slice(0, 80);

  if (md) out.push(`# ${INFO?.title || INFO?.name || "译文"}\n`);

  right.querySelectorAll(".pg").forEach((pg) => {
    const paras = pg.querySelectorAll(".para");
    if (!paras.length) return;
    const no = pg.id.slice(1);
    out.push(md ? `\n## 第 ${no} 页\n` : `\n========== 第 ${no} 页 ==========\n`);
    paras.forEach((p) => {
      if (p.classList.contains("err")) return;
      // textContent 能取到隐藏的原文
      const dst = (p.querySelector(".dst")?.textContent || "").trim();
      if (!dst) return;
      count++;
      if (bi) {
        const src = (p.querySelector(".src")?.textContent || "").trim();
        out.push(md ? `${dst}\n\n> ${src}\n` : `${dst}\n【原】${src}\n`);
      } else {
        out.push(dst + "\n");
      }
    });
  });

  if (!count) {
    hint.textContent = "当前页面没有译文内容";
    hint.className = "hint bad";
    return;
  }

  const blob = new Blob([out.join("\n")], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${title}-译文.${md ? "md" : "txt"}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);

  hint.textContent = `已导出 ${count} 段` + (failed || incomplete ? "（内容不完整）" : "");
  hint.className = "hint ok";
}

/* ---------------- 设置 ---------------- */
let CFG = null;

async function openCfg() {
  $("#cfg").classList.add("on");
  $("#cfgHint").textContent = "加载中…";
  $("#cfgHint").className = "hint";
  loadParse();
  try {
    CFG = await (await fetch("/api/settings")).json();
  } catch (e) {
    $("#cfgHint").textContent = "读取设置失败：" + e;
    $("#cfgHint").className = "hint bad";
    return;
  }
  $("#cfgProvider").innerHTML = CFG.items.map((it) =>
    `<option value="${esc(it.provider)}"${it.provider === CFG.current ? " selected" : ""}>`
    + `${esc(it.provider)} — ${esc(it.model)}</option>`).join("");
  syncCfgHint();
}

function syncCfgHint() {
  const it = (CFG?.items || []).find((x) => x.provider === $("#cfgProvider").value);
  const h = $("#cfgHint"), inp = $("#cfgKey");
  if (!it) { h.textContent = ""; return; }
  inp.value = "";
  if (!it.needs_key) {
    inp.disabled = true;
    inp.placeholder = "该服务无需密钥";
    h.textContent = "本地服务，无需配置密钥";
    h.className = "hint ok";
    return;
  }
  inp.disabled = false;
  inp.placeholder = it.configured ? "已配置（留空则保持不变）" : "粘贴密钥后点保存";
  if (it.from_env) {
    h.textContent = `已由环境变量 ${it.key_env} 提供，优先生效`;
    h.className = "hint ok";
  } else if (it.configured) {
    h.textContent = `${it.key_env} 已保存在本机`;
    h.className = "hint ok";
  } else {
    h.textContent = `尚未配置 ${it.key_env}`;
    h.className = "hint bad";
  }
}

async function saveCfg() {
  const body = { provider: $("#cfgProvider").value };
  const k = $("#cfgKey").value.trim();
  if (k) body.key = k;
  $("#cfgHint").textContent = "保存中…";
  $("#cfgHint").className = "hint";
  try {
    const r = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    INFO = await r.json();
    $("#cfgKey").value = "";
    CFG = await (await fetch("/api/settings")).json();
    syncCfgHint();
    $("#cfgHint").textContent = "已保存";
    $("#cfgHint").className = "hint ok";
    if (INFO.loaded) refreshMeta();
    setTimeout(() => $("#cfg").classList.remove("on"), 600);
  } catch (e) {
    $("#cfgHint").textContent = "保存失败：" + e;
    $("#cfgHint").className = "hint bad";
  }
}

function refreshMeta() {
  const tag = INFO.paper_mode ? " · 论文模式" : "";
  $("#docMeta").textContent =
    `${INFO.pages} 页 · ${(INFO.chars / 10000).toFixed(1)} 万字符待译 · `
    + `${INFO.paras} 段 · ${INFO.provider}/${INFO.model}${tag}`;
  if (!INFO.ready) {
    $("#docMeta").innerHTML +=
      ` <span style="color:var(--warn)">· ${esc(INFO.warn)}</span>`;
  }
}

$("#btnCfg").onclick = openCfg;
$("#cfgClose").onclick = () => $("#cfg").classList.remove("on");
$("#cfgEye").onclick = () => {
  const inp = $("#cfgKey"), eye = $("#cfgEye");
  const show = inp.type === "password";
  inp.type = show ? "text" : "password";
  eye.textContent = show ? "隐藏" : "显示";
};
$("#cfgSave").onclick = saveCfg;
$("#cfgProvider").onchange = syncCfgHint;
$("#cfg").addEventListener("click", (e) => {
  if (e.target.id === "cfg") $("#cfg").classList.remove("on");
});
$("#cfgKey").addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveCfg();
});
/* ---------------- 论文模式设置 ---------------- */
const PM_BOXES = {
  cbFig: "translate_figtext",
  cbCap: "translate_caption",
  cbRefs: "skip_refs",
  cbTab: "skip_tables",
  cbMath: "mask_math",
  cbFold: "fold_formula",
};
let PARSE = null;

async function loadParse() {
  try {
    PARSE = await (await fetch("/api/parse-settings")).json();
  } catch (e) {
    $("#pmHint").textContent = "读取失败：" + e;
    $("#pmHint").className = "hint bad";
    return;
  }
  const o = PARSE.options;
  syncSeg("segPaper", o.paper_mode);
  for (const [id, key] of Object.entries(PM_BOXES)) $("#" + id).checked = !!o[key];
  const on = PARSE.applied_paper_mode;
  const b = $("#pmState");
  b.textContent = on ? "当前已生效" : "当前未生效";
  b.className = "badge" + (on ? " on" : "");
  $("#pmHint").textContent =
    "论文模式识别图表区域，跳过坐标轴/图例等碎片，"
    + "并把被插图和公式截断的段落重新缝合。";
  $("#pmHint").className = "hint";
}

async function saveParse(patch) {
  $("#pmHint").textContent = "应用中…";
  $("#pmHint").className = "hint";
  try {
    const r = await fetch("/api/parse-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    PARSE = { options: d.options, applied_paper_mode: d.applied_paper_mode };
    syncSeg("segPaper", d.options.paper_mode);
    for (const [id, key] of Object.entries(PM_BOXES)) {
      $("#" + id).checked = !!d.options[key];
    }
    const b = $("#pmState");
    b.textContent = d.applied_paper_mode ? "当前已生效" : "当前未生效";
    b.className = "badge" + (d.applied_paper_mode ? " on" : "");

    if (d.info && d.info.loaded) {
      INFO = d.info;
      refreshMeta();
      // 结构变化时清空已有译文, 避免与新解析结果错位
      if (d.reloaded) {
        if (es) { es.close(); es = null; }
        running = false;
        $("#btnRun").textContent = "开始翻译";
        right.innerHTML =
          `<div class="empty">解析已更新，请重新点击 <b>开始翻译</b></div>`;
        buildLeft();
      }
    }
    $("#pmHint").textContent = d.reloaded ? "已重新解析文档" : "已保存";
    $("#pmHint").className = "hint ok";
  } catch (e) {
    $("#pmHint").textContent = "应用失败：" + e;
    $("#pmHint").className = "hint bad";
  }
}

document.querySelectorAll("#segPaper button").forEach((b) => {
  b.onclick = () => saveParse({ paper_mode: b.dataset.v });
});
for (const [id, key] of Object.entries(PM_BOXES)) {
  $("#" + id).onchange = (e) => saveParse({ [key]: e.target.checked });
}

$("#btnQuit").onclick = async () => {
  if (running && !confirm("翻译正在进行中，确定退出吗？")) return;
  else if (!running && !confirm("确定退出 pdfread 吗？")) return;

  if (es) { es.close(); es = null; }
  $("#bye").classList.add("on");
  document.title = "pdfread 已退出";
  try {
    await fetch("/api/shutdown", { method: "POST" });
  } catch (e) { /* 服务已停止, 连接被断开属正常 */ }
};

$("#btnTheme").onclick = () => {
  const i = THEME_ORDER.indexOf(prefs.theme);
  prefs.theme = THEME_ORDER[(i + 1) % THEME_ORDER.length];
  savePrefs();
  applyPrefs();
};

/* 顶栏字号滑块: 拖动即时预览, 松手保存 */
$("#fontRange").addEventListener("input", (e) => {
  prefs.font = e.target.value;
  document.documentElement.style.setProperty("--rfs", prefs.font + "px");
  $("#fontVal").textContent = prefs.font;
});
$("#fontRange").addEventListener("change", () => {
  savePrefs();
  applyPrefs();
});
$("#cbItalic").onchange = (e) => {
  prefs.italicSrc = e.target.checked;
  savePrefs();
  applyPrefs();
};

/* PDF 缩放(60%–250%): 放大后按新宽度重取高清图; 划线按百分比定位, 不错位 */
/* 放大超出栏宽时 margin:auto 会失效(内容贴左溢出), 需要把横向滚动位置置中 */
function centerLeft() {
  if (left.scrollWidth > left.clientWidth) {
    left.scrollLeft = (left.scrollWidth - left.clientWidth) / 2;
  }
}

function setZoom(v) {
  prefs.zoom = Math.min(2.5, Math.max(0.6, Math.round(v * 10) / 10));
  savePrefs();
  applyPrefs();
  setTimeout(() => {
    centerLeft();
    if (leftObserver && INFO && INFO.fmt !== "epub") {
      left.querySelectorAll(".pg img").forEach((i) => leftObserver.observe(i));
    }
  }, 60);
}
$("#zoomIn").onclick = () => setZoom((prefs.zoom || 1) + 0.1);
$("#zoomOut").onclick = () => setZoom((prefs.zoom || 1) - 0.1);
$("#zoomVal").onclick = () => setZoom(1);

$("#btnExport").onclick = doExport;
$("#btnExp").onclick = doExport;

/* 启动: 先恢复阅读偏好, 再加载 */
loadPrefs();
applyPrefs();
segInit("segTheme", "theme");
segInit("segFont", "font");
segInit("segLayout", "layout");
segInit("segExpFmt", "expFmt");
segInit("segExpSrc", "expSrc");

init().catch((e) => setBusy(String(e), true));
