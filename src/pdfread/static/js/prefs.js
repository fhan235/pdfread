/* ---------------- 阅读偏好(存 localStorage) ---------------- */
const PREF_KEY = "pdfread.prefs";
let prefs = { theme: "dark", font: "15", layout: "side", layoutPrev: "side",
              expFmt: "txt", expSrc: "dst", italicSrc: false, lib: false,
              zoom: 1, qa: false, leftPct: 0, libW: 320, qaW: 340,
              toc: false, efont: 16, eline: 1.9, efontFam: "serif",
              efontCustom: "" };

/* 书籍(EPUB/TXT)字体预设 */
const EFONT_STACKS = {
  serif: 'Georgia, "Songti SC", "SimSun", serif',
  sans: '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
  kai: '"Kaiti SC", "STKaiti", "KaiTi", serif',
  fangsong: '"FangSong", "STFangsong", "FangSong_GB2312", serif',
};
function ebookFont() {
  if (prefs.efontFam === "custom") {
    // 用户输入的系统字体名, 过滤引号/分号防 CSS 注入
    const name = (prefs.efontCustom || "").replace(/[;{}<>"]/g, "").trim();
    if (name) return `"${name}", serif`;
  }
  return EFONT_STACKS[prefs.efontFam] || EFONT_STACKS.serif;
}

function loadPrefs() {
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); }
  catch (e) { /* 忽略损坏的偏好 */ }
}
function savePrefs() {
  localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
}
const THEME_ORDER = ["dark", "light", "paper", "green"];
const THEME_NAMES = { dark: "深色", light: "浅色", paper: "纸张", green: "护眼",
                      custom: "自定义" };
/* 自定义主题可调的 8 个配色变量 */
const THEME_VARS = ["bg", "panel", "line", "fg", "dim", "accent", "ok", "warn"];
const VAR_NAMES = { bg: "背景", panel: "面板", line: "边框", fg: "文字",
                    dim: "次要文字", accent: "强调色", ok: "成功", warn: "警告" };

/* 按背景亮度决定自定义主题套用深色还是浅色的组件适配 */
function isLightColor(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return false;
  const n = parseInt(m[1], 16);
  const lum = 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
  return lum > 140;
}

function applyCustomTheme() {
  const custom = prefs.theme === "custom";
  // 首次进入自定义: 以当前主题为底版, 快照一份配色再改
  if (custom && !prefs.themeCustom) {
    const cs = getComputedStyle(document.body);
    prefs.themeCustom = {};
    for (const v of THEME_VARS)
      prefs.themeCustom[v] = cs.getPropertyValue("--" + v).trim() || "#888888";
    savePrefs();
  }
  for (const v of THEME_VARS) document.body.style.removeProperty("--" + v);
  if (custom) {
    for (const [k, val] of Object.entries(prefs.themeCustom || {}))
      if (THEME_VARS.includes(k)) document.body.style.setProperty("--" + k, val);
  }
  const grid = $("#themeGrid");
  if (grid) grid.style.display = custom ? "" : "none";
  return custom;
}

function buildThemeGrid() {
  const grid = $("#themeGrid .clrgrid");
  if (!grid) return;
  grid.innerHTML = THEME_VARS.map((v) =>
    `<label>${VAR_NAMES[v]}<input type="color" data-var="${v}"></label>`
  ).join("");
  grid.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("input", () => {
      prefs.themeCustom[inp.dataset.var] = inp.value;
      document.body.style.setProperty("--" + inp.dataset.var, inp.value);
    });
    inp.addEventListener("change", () => { savePrefs(); applyPrefs(); });
  });
}

function syncThemeGrid() {
  document.querySelectorAll("#themeGrid input[data-var]").forEach((inp) => {
    const v = prefs.themeCustom?.[inp.dataset.var];
    if (/^#[0-9a-f]{6}$/i.test(v || "")) inp.value = v;
  });
}

function applyPrefs() {
  // 先快照/应用自定义配色(需在切换 class 之前读取当前主题的变量值)
  const customTheme = applyCustomTheme();
  // paper / green 属于浅色体系: 复用 body.light 的组件适配, 只换配色变量
  // 自定义主题按背景亮度决定套用深色还是浅色的组件适配
  document.body.classList.toggle("light", customTheme
    ? isLightColor(prefs.themeCustom?.bg) : prefs.theme !== "dark");
  document.body.classList.toggle("theme-paper",
    !customTheme && prefs.theme === "paper");
  document.body.classList.toggle("theme-green",
    !customTheme && prefs.theme === "green");
  document.body.classList.toggle("italic-src", !!prefs.italicSrc);
  document.documentElement.style.setProperty("--rfs", prefs.font + "px");
  document.documentElement.style.setProperty("--efs", (prefs.efont || 16) + "px");
  document.documentElement.style.setProperty("--elh", prefs.eline || 1.9);
  document.documentElement.style.setProperty("--eff", ebookFont());
  const eff = $("#efontFamily");
  if (eff) eff.value = prefs.efontFam || "serif";
  const efc = $("#efontCustom");
  if (efc) {
    efc.value = prefs.efontCustom || "";
    efc.style.display = prefs.efontFam === "custom" ? "" : "none";
  }
  const ef = $("#efontRange");
  if (ef) ef.value = prefs.efont || 16;
  const efv = $("#efontVal");
  if (efv) efv.textContent = prefs.efont || 16;
  const el = $("#elineRange");
  if (el) el.value = prefs.eline || 1.9;
  const elv = $("#elineVal");
  if (elv) elv.textContent = prefs.eline || 1.9;
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
  if (customTheme) syncThemeGrid();
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
  if (UPDATE && UPDATE.current)
    $("#verNow").textContent = "版本 v" + UPDATE.current;
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
  const cp = $("#cfgProxy");
  if (cp && !cp.value) cp.value = CFG.proxy || "";
  syncCfgHint();
}

function syncCfgHint() {
  const it = (CFG?.items || []).find((x) => x.provider === $("#cfgProvider").value);
  const h = $("#cfgHint"), inp = $("#cfgKey");
  // 模型: 下拉选择预设候选, 选「自定义…」时出现输入框; 空值回落到默认模型
  const sel = $("#cfgModelSel"), mi = $("#cfgModel");
  const models = it?.models || [];
  const custom = it?.custom_model || "";
  sel.innerHTML = `<option value="">默认: ${esc(it?.model || "")}</option>`
    + models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("")
    + `<option value="__custom__">自定义…</option>`;
  if (custom && !models.includes(custom)) {
    sel.value = "__custom__";
    mi.style.display = "";
    mi.value = custom;
  } else {
    sel.value = custom;
    mi.style.display = "none";
    mi.value = "";
  }
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

$("#cfgModelSel").addEventListener("change", () => {
  const custom = $("#cfgModelSel").value === "__custom__";
  const mi = $("#cfgModel");
  mi.style.display = custom ? "" : "none";
  if (custom) mi.focus();
});

/* 取当前选择的模型名: 下拉值, 自定义时取输入框 */
function cfgModelValue() {
  const sel = $("#cfgModelSel");
  return sel.value === "__custom__" ? $("#cfgModel").value.trim() : sel.value;
}

async function saveCfg() {
  const body = { provider: $("#cfgProvider").value,
                 model: cfgModelValue() };
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
  syncEreadBtn();
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

/* 书籍(EPUB/TXT)字号与行距: 拖动即时预览, 松手保存 */
$("#efontRange").addEventListener("input", (e) => {
  prefs.efont = e.target.value;
  document.documentElement.style.setProperty("--efs", prefs.efont + "px");
  $("#efontVal").textContent = prefs.efont;
});
$("#efontRange").addEventListener("change", () => { savePrefs(); });
$("#elineRange").addEventListener("input", (e) => {
  prefs.eline = e.target.value;
  document.documentElement.style.setProperty("--elh", prefs.eline);
  $("#elineVal").textContent = prefs.eline;
});
$("#elineRange").addEventListener("change", () => { savePrefs(); });

/* 书籍排版按钮: 仅 EPUB/TXT(可重排格式)时出现在文档标题栏 */
function syncEreadBtn() {
  const b = $("#btnEread");
  if (b) b.style.display =
    (INFO && INFO.loaded && INFO.fmt && INFO.fmt !== "pdf") ? "" : "none";
  if (!b || b.style.display === "none") $("#ereadPop")?.classList.remove("on");
}
$("#btnEread").onclick = (e) => {
  e.stopPropagation();
  $("#ereadPop").classList.toggle("on");
};
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#ereadPop") && !e.target.closest("#btnEread"))
    $("#ereadPop")?.classList.remove("on");
});

/* 书籍字体: 预设/自定义 */
$("#efontFamily").onchange = (e) => {
  prefs.efontFam = e.target.value;
  savePrefs();
  applyPrefs();
};
$("#efontCustom").addEventListener("change", (e) => {
  prefs.efontCustom = e.target.value;
  savePrefs();
  applyPrefs();
});

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
  setTimeout(() => centerLeft(), 60);   // SVG 矢量图无需重取, 只需居中
}
$("#zoomIn").onclick = () => setZoom((prefs.zoom || 1) + 0.1);
$("#zoomOut").onclick = () => setZoom((prefs.zoom || 1) - 0.1);
$("#zoomVal").onclick = () => setZoom(1);

/* Ctrl/Cmd + 滚轮: 只缩放 PDF 栏; 同时拦掉浏览器整页缩放, 避免两栏比例错位。
   触控板捏合会产生带 ctrlKey 的高频小步进 wheel 事件, 累加换算成 10% 档位 */
let wheelAcc = 0;
window.addEventListener("wheel", (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  if (!left.contains(e.target)) return;   // 光标不在 PDF 栏时仅拦截整页缩放
  wheelAcc += e.deltaY * (e.deltaMode === 1 ? 33 : 1);  // 行模式换算成像素
  while (Math.abs(wheelAcc) >= 50) {
    setZoom((prefs.zoom || 1) + (wheelAcc < 0 ? 0.1 : -0.1));
    wheelAcc += wheelAcc < 0 ? 50 : -50;
  }
}, { passive: false });

$("#btnExport").onclick = doExport;
$("#btnExp").onclick = doExport;

/* 恢复默认: 清掉自定义配色, 以深色主题为底版重新快照 */
$("#btnThemeReset").onclick = () => {
  for (const v of THEME_VARS) document.body.style.removeProperty("--" + v);
  prefs.themeCustom = null;
  savePrefs();
  applyPrefs();
};

/* 版本更新: 启动后静默检查 GitHub 最新 Release, 有新版则顶栏出现提示 */
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
setTimeout(() => checkUpdate(false), 2000);

/* 启动: 先恢复阅读偏好, 再加载 */
buildThemeGrid();
loadPrefs();
applyPrefs();
segInit("segTheme", "theme");
segInit("segFont", "font");
segInit("segLayout", "layout");
segInit("segExpFmt", "expFmt");
segInit("segExpSrc", "expSrc");

init().catch((e) => setBusy(String(e), true));
