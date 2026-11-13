/* ---------------- 面板拖拽调宽 ---------------- */
/* leftPct: 左栏占比(%), 0 表示默认均分; 上下布局时同一数值作用于高度 */
function applySplit() {
  const custom = prefs.leftPct
    && (prefs.layout === "side" || prefs.layout === "stack");
  if (custom) {
    left.style.flex = `0 0 ${prefs.leftPct}%`;
    right.style.flex = "1 1 0";
    left.style.maxHeight = "none";   // 覆盖上下布局的 50% 限高
  } else {
    left.style.flex = "";
    right.style.flex = "";
    left.style.maxHeight = "";
  }
  $("#libView").style.width = (prefs.libW || 320) + "px";
  $("#qaPanel").style.width = (prefs.qaW || 340) + "px";
  $("#qaIn").style.height = (prefs.qaInH || 56) + "px";
}

function dragResize(splitter, onMove) {
  splitter.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    splitter.classList.add("drag");
    const move = (ev) => onMove(ev);
    const up = () => {
      splitter.classList.remove("drag");
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      savePrefs();
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

dragResize($("#splitLR"), (e) => {
  const r = document.querySelector("main").getBoundingClientRect();
  const pct = prefs.layout === "stack"
    ? (e.clientY - r.top) / r.height * 100
    : (e.clientX - r.left) / r.width * 100;
  prefs.leftPct = Math.round(Math.min(85, Math.max(15, pct)) * 10) / 10;
  applySplit();
});
dragResize($("#splitLib"), (e) => {
  prefs.libW = Math.round(Math.min(560, Math.max(220, e.clientX)));
  applySplit();
});
dragResize($("#splitQA"), (e) => {
  prefs.qaW = Math.round(Math.min(680, Math.max(260, window.innerWidth - e.clientX)));
  applySplit();
});
/* 问答输入框高度: 拖输入区上沿, 上限为面板高度的 70% */
dragResize($("#splitQaIn"), (e) => {
  const bar = $(".qabar").getBoundingClientRect();
  const panelH = $("#qaPanel").getBoundingClientRect().height;
  const h = bar.bottom - e.clientY - 22;   // 扣除 .qabar 上下内边距与边框
  prefs.qaInH = Math.round(Math.min(panelH * 0.7, Math.max(40, h)));
  applySplit();
});

/* ---------------- AI 问答 ---------------- */
/* 对话按文库文档 id 持久化在 library.db(表 qa_sessions), 刷新页面、重开
   应用都还在; 标签快照只作为同会话内切换时的内存缓存。
   qaHist 始终指向当前会话的历史数组(同一数组对象, 不可重新赋值成副本)。
   问答不走缓存, 每次都发送全文。同一时刻只允许一个请求在跑(qaBusy)。

   未入库的文档(INFO.lib 为空, 如临时打开的文件)退化为纯内存会话:
   问答照常可用, 只是刷新后不保留。 */
let qaSessions = [];   // [{id, hist:[{role,content}], saved:bool}]
let qaSid = 0;         // 当前会话 id
let qaSeq = 0;         // 本地新建会话的临时 id(负数, 与服务端 id 区分)
let qaHist = [];       // === 当前会话的 hist
let qaBusy = false;
let qaBusySid = 0;     // 正在等待回答的会话, 用于在标签上标记 ●
let qaLoadedLib = null;   // 已加载会话的文库 id, 避免重复拉取

/* 会话标题: 取首条用户提问, 没有则「新对话」。
   maxLen 小一些: 标签很窄, 过长的标题只会被省略号截掉 */
function qaSessTitle(s, maxLen) {
  const first = (s.hist || []).find((m) => m.role === "user");
  const t = first ? first.content.replace(/\s+/g, " ").trim() : "";
  const n = maxLen || 20;
  return t ? (t.length > n ? t.slice(0, n) + "…" : t) : "新对话";
}

/* 新建会话并切到它; 返回新会话。
   本地 id 用递减负数, 首次保存后换成服务端返回的正 id */
function qaNewSession() {
  const s = { id: --qaSeq, hist: [] };
  qaSessions.push(s);
  qaSid = s.id;
  qaHist = s.hist;
  return s;
}

/* 把当前会话写回服务端(节流: 回答结束/删除时才调, 不是每个字都存) */
async function qaPersist(session) {
  const lib = INFO && INFO.lib;
  if (!lib || !session) return;
  try {
    const r = await libPost("/api/qa/sessions", {
      lib, id: session.id > 0 ? session.id : null,
      hist: session.hist, title: qaSessTitle(session, 40),
    });
    if (!r.ok) return;
    const d = await r.json();
    // 新建的会话拿到服务端 id 后要换过来, 否则下次会重复插入
    if (d.id && session.id !== d.id) {
      const wasCurrent = qaSid === session.id;
      session.id = d.id;
      if (wasCurrent) qaSid = d.id;
      renderQaSessions();
    }
  } catch (e) { /* 存不上不影响当前对话 */ }
}

/* 进入文档时加载该文档的历史对话; 没有则开一个空会话 */
async function qaLoadDoc() {
  const lib = (INFO && INFO.lib) || 0;
  qaLoadedLib = lib;
  qaSessions = [];
  qaSeq = 0;
  if (lib) {
    try {
      const d = await (await fetch("/api/qa/sessions?lib=" + lib)).json();
      qaSessions = (d.sessions || []).map((s) => ({ id: s.id, hist: s.hist || [] }));
    } catch (e) { /* 读不到就当没有历史 */ }
  }
  if (!qaSessions.length) qaNewSession();
  const cur = qaSessions[qaSessions.length - 1];
  qaSid = cur.id;
  qaHist = cur.hist;
  renderQA();
}

/* 标签内缓存: 切标签时不必重新请求 */
function qaLoadTab(t) {
  const lib = (INFO && INFO.lib) || 0;
  if (t && t.qaSessions && t.qaLib === lib) {
    qaSessions = t.qaSessions;
    const s = qaSessions.find((x) => x.id === t.qaSid) || qaSessions[0];
    qaSid = s.id;
    qaHist = s.hist;
    qaLoadedLib = lib;
    return;
  }
  // 没有缓存(新开/刷新): 异步从服务端拉, 先给个空会话占位
  qaSessions = [];
  qaSeq = 0;
  qaNewSession();
  qaLoadDoc();
}

/* 写回标签快照 */
function qaSaveTab(t) {
  if (!t) return;
  t.qaSessions = qaSessions;
  t.qaSid = qaSid;
  t.qaLib = (INFO && INFO.lib) || 0;
}

/* 渲染对话标签栏(横向, 像浏览器标签): 当前高亮, 等待回答的显示 ●,
   每个标签自带 ×。只剩一个空对话时不给删(删了也要立刻新建一个) */
function renderQaSessions() {
  const box = $("#qaTabs");
  const only = qaSessions.length === 1;
  box.innerHTML = qaSessions.map((s, i) => {
    const on = s.id === qaSid;
    const busy = s.id === qaBusySid;
    const title = qaSessTitle(s, 14);
    const canDel = !(only && !(s.hist || []).length);
    return `<div class="qtab${on ? " on" : ""}" data-sid="${s.id}"`
      + ` title="${esc(qaSessTitle(s, 40))}">`
      + (busy ? `<span class="dot">●</span>` : "")
      + `<span class="nm">${esc(title)}</span>`
      + (canDel ? `<button class="x" data-del="${s.id}"`
        + ` title="关闭该对话">×</button>` : "")
      + `</div>`;
  }).join("");
  // 切换后让当前标签滚进视野(标签多时会横向溢出)
  const cur = box.querySelector(".qtab.on");
  if (cur && cur.scrollIntoView) {
    cur.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

/* 状态条: 平时不占垂直空间, 有消息才出现, 片刻后自动消失 */
let qaStatusTimer = null;
function qaStatus(msg, keep) {
  const el = $("#qaHint");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("on", !!msg);
  clearTimeout(qaStatusTimer);
  if (msg && !keep) {
    qaStatusTimer = setTimeout(() => {
      el.textContent = "";
      el.classList.remove("on");
    }, 3200);
  }
}

/* 空对话时的说明: 放在对话区内部, 有内容后自动让位,
   不像原来那样用一条常驻横幅永久占掉 ~52px */
function qaEmptyHint() {
  const box = $("#qaList");
  if (!box) return;
  const old = box.querySelector(".qaempty");
  if (qaHist.length) { if (old) old.remove(); return; }
  if (old) return;
  const d = document.createElement("div");
  d.className = "qaempty";
  const ready = INFO && INFO.loaded;
  d.innerHTML = `<div class="ttl">${ready ? "就这篇文档提问" : "先打开一篇文档"}</div>`
    + `<div class="sub">${ready
      ? `已载入全文约 ${esc(((INFO.chars || 0) / 10000).toFixed(1))} 万字符。`
        + `每次提问都会把正文发送给翻译服务，超长只保留首尾。`
      : "打开后即可基于全文问答，或一键生成速读摘要。"}</div>`;
  box.appendChild(d);
}

function toggleQA() {
  const on = !document.body.classList.contains("qa");
  document.body.classList.toggle("qa", on);
  $("#btnQA").classList.toggle("on", on);
  prefs.qa = on;
  savePrefs();
  if (on) { renderQA(); loadQaModel(); $("#qaIn").focus(); }
}

/* 极简 Markdown 渲染(所有文本先 esc 转义再拼标签, 防注入):
   支持 # 标题、**粗体**、*斜体*、`行内代码`、无序/有序列表、
   ``` 代码块、| 表格 |、> 引用、--- 分割线、
   数学公式($...$ / \(...\) 行内, $$...$$ / \[...\] 独立成块, 见 mathtex.js)。
   容错: 表格行/列表项之间夹空行不断开(模型常这样输出); 代码块与公式块未闭合(流式中)渲染到末尾 */
const MD_UL = /^\s*[-*+•]\s+(.*)/;
const MD_OL = /^\s*(\d+)[.、)]\s+(.*)/;
const MD_HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
const MD_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
/* 行内代码与行内公式: 整体切出, 内部不参与 Markdown 变换。
   行内公式要求 $ 紧邻非空白, 避免把「$5 … $10」这类金额当成公式 */
const MD_SPAN = /(`[^`]+`|\$\$[\s\S]+?\$\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|\$(?!\s)(?:\\.|[^\n$\\])+?(?<![\s\\])\$)/;
const MD_SPAN_G = new RegExp(MD_SPAN.source, "g");

function mdRender(src) {
  /* 行内变换: 先把代码/公式整段换成占位符, 再做 **粗体** / *斜体*。
     不能先按公式 split 再逐段套正则 —— 模型很爱写
     `**$d^2_{\Pi,i}$：点到切平面的距离**`, 那样 ** 的开闭会落到
     公式两侧的不同片段里, 永远配不上对, 于是 ** 被原样显示出来。
     占位符让粗斜体可以跨公式配对, 同时公式内部仍不参与 Markdown 变换。 */
  const inline = (s) => {
    const kept = [];
    const masked = String(s).replace(MD_SPAN_G,
      (m) => `\u0000${kept.push(m) - 1}\u0000`);
    return esc(masked)
      // 要求紧邻非空白, 避免把 "a * b * c" 这类乘号当成强调
      .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, "$1<i>$2</i>")
      .replace(/\u0000(\d+)\u0000/g, (_, n) => {
        const seg = kept[+n];
        if (seg[0] === "`") return `<code>${esc(seg.slice(1, -1))}</code>`;
        if (seg.startsWith("$$")) return texRender(seg.slice(2, -2), true);
        if (seg.startsWith("\\[")) return texRender(seg.slice(2, -2), true);
        if (seg.startsWith("\\(")) return texRender(seg.slice(2, -2), false);
        return texRender(seg.slice(1, -1), false);
      });
  };
  const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
  const nextNonBlank = (k) => {
    while (k < lines.length && !lines[k].trim()) k++;
    return k;
  };
  const listType = (l) => (MD_OL.test(l) ? "ol" : MD_UL.test(l) && !MD_HR.test(l) ? "ul" : null);
  const isSep = (l) => l.includes("|") && MD_SEP.test(l);
  const cells = (l) => {
    // 先把行内代码/公式整段换成占位符, 防止其中的 | 把单元格切坏
    const kept = [];
    let s = l.trim().replace(MD_SPAN_G, (mm) => `\u0000${kept.push(mm) - 1}\u0000`);
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|")
      .replace(/\u0000(\d+)\u0000/g, (_, n) => kept[+n]));
  };
  const out = [];
  let list = null;
  const closeList = () => {
    if (list) { out.push(list === "ul" ? "</ul>" : "</ol>"); list = null; }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    let m;
    if (!line.trim()) {
      // 同类列表项之间的空行不打断列表, 否则有序列表编号会从 1 重来
      const n = nextNonBlank(i + 1);
      if (n >= lines.length || listType(lines[n]) !== list) closeList();
      continue;
    }
    if ((m = line.match(/^\s*(`{3,}|~{3,})/))) {
      closeList();
      const fence = m[1], body = [];
      for (i++; i < lines.length; i++) {
        const t = lines[i].trim();
        if (t.startsWith(fence) && /^[`~]+$/.test(t)) break;
        body.push(lines[i]);
      }
      out.push(`<pre class="md-pre"><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }
    // 独立公式块: $$ … $$ / \[ … \], 允许跨行(模型常把 $$ 单独放一行)
    if ((m = line.match(/^\s*(\$\$|\\\[)/))) {
      closeList();
      const open = m[1], close = open === "$$" ? "$$" : "\\]";
      const rest = line.trim().slice(open.length);
      const e0 = rest.indexOf(close);
      let body = rest;
      if (e0 >= 0) {
        body = rest.slice(0, e0);
      } else {
        const acc = [rest];
        for (i++; i < lines.length; i++) {
          const e = lines[i].indexOf(close);
          if (e >= 0) { acc.push(lines[i].slice(0, e)); break; }
          acc.push(lines[i]);
        }
        body = acc.join("\n");
      }
      out.push(`<div class="md-math">${texRender(body, true)}</div>`);
      continue;
    }
    if (line.includes("|")) {
      const s = nextNonBlank(i + 1);
      if (s < lines.length && isSep(lines[s])) {
        closeList();
        const head = cells(line);
        const align = cells(lines[s]).map((c) => (c.endsWith(":")
          ? (c.startsWith(":") ? "center" : "right")
          : (c.startsWith(":") ? "left" : "")));
        const rows = [];
        i = s;
        for (;;) {
          const n = nextNonBlank(i + 1);
          if (n >= lines.length) break;
          const t = lines[n];
          // 隔着空行的续行必须以 | 开头, 防止把表后正文吞进表格
          if (!t.includes("|") || isSep(t)
              || (n > i + 1 && !t.trim().startsWith("|"))) break;
          rows.push(cells(t));
          i = n;
        }
        const tr = (tag, cs) => "<tr>" + head.map((_, k) => {
          const a = align[k] ? ` style="text-align:${align[k]}"` : "";
          return `<${tag}${a}>${inline(cs[k] || "")}</${tag}>`;
        }).join("") + "</tr>";
        out.push(`<div class="md-table"><table><thead>${tr("th", head)}</thead>`
          + `<tbody>${rows.map((r) => tr("td", r)).join("")}</tbody></table></div>`);
        continue;
      }
    }
    if (MD_HR.test(line)) {
      closeList(); out.push("<hr>");
    } else if ((m = line.match(/^(#{1,6})\s+(.*)/))) {
      closeList();
      const cls = m[1].length <= 2 ? "md-h3" : "md-h4";
      out.push(`<div class="${cls}">${inline(m[2])}</div>`);
    } else if (/^\s*>/.test(line)) {
      closeList();
      const quote = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) {
        quote.push(inline(lines[i].replace(/^\s*>\s?/, "").trimEnd()));
      }
      i--;
      out.push(`<blockquote>${quote.join("<br>")}</blockquote>`);
    } else if ((m = line.match(MD_OL))) {
      if (list !== "ol") {
        closeList();
        const start = parseInt(m[1], 10);
        out.push(start > 1 ? `<ol start="${start}">` : "<ol>");
        list = "ol";
      }
      out.push(`<li>${inline(m[2])}</li>`);
    } else if ((m = line.match(MD_UL))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else {
      closeList(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("");
}

function addQaBubble(role, text) {
  const d = document.createElement("div");
  d.className = "qmsg " + (role === "user" ? "user" : "bot");
  if (role === "user") d.textContent = text;   // 用户输入纯文本
  else d.innerHTML = mdRender(text);           // AI 回答按 Markdown 渲染
  const list = $("#qaList");
  const empty = list.querySelector && list.querySelector(".qaempty");
  if (empty) empty.remove();                   // 有内容了, 空状态让位
  list.appendChild(d);
  list.scrollTop = list.scrollHeight;
  return d;
}

function renderQA() {
  if (!qaSessions.length) qaNewSession();
  renderQaSessions();
  const box = $("#qaList");
  box.innerHTML = "";
  for (const m of qaHist) addQaBubble(m.role, m.content);
  qaEmptyHint();
}

/* 切换/新建/删除会话: 只换视图, 不影响正在跑的请求(它写进自己捕获的历史数组) */
function qaSwitchSession(id) {
  const s = qaSessions.find((x) => x.id === Number(id));
  if (!s || s.id === qaSid) return;
  qaSid = s.id;
  qaHist = s.hist;
  qaSaveTab(curTab());
  renderQA();
}

/* 关闭某个对话; 关掉当前的就切到相邻一个 */
function qaDelSession(id) {
  const s = qaSessions.find((x) => x.id === Number(id));
  if (!s) return;
  if ((s.hist || []).length
      && !confirm(`关闭对话「${qaSessTitle(s)}」？\n该对话的问答记录会删除且不可恢复。`)) return;
  const i = qaSessions.indexOf(s);
  qaSessions.splice(i, 1);
  // 已落库的会话要同步删掉, 否则刷新后又回来了
  if (s.id > 0 && INFO && INFO.lib) {
    libPost("/api/qa/sessions/delete", { lib: INFO.lib, id: s.id }).catch(() => {});
  }
  if (!qaSessions.length) qaNewSession();
  else if (s.id === qaSid) {
    const next = qaSessions[Math.min(i, qaSessions.length - 1)];
    qaSid = next.id;
    qaHist = next.hist;
  }
  qaSaveTab(curTab());
  renderQA();
}

/* 标签栏事件委托: × 关闭, 其余位置切换 */
$("#qaTabs").addEventListener("click", (e) => {
  const x = e.target.closest("[data-del]");
  if (x) { e.stopPropagation(); qaDelSession(x.dataset.del); return; }
  const tab = e.target.closest("[data-sid]");
  if (tab) qaSwitchSession(tab.dataset.sid);
});
/* 中键关闭, 和浏览器标签一致 */
$("#qaTabs").addEventListener("auxclick", (e) => {
  if (e.button !== 1) return;
  const tab = e.target.closest("[data-sid]");
  if (!tab) return;
  e.preventDefault();
  qaDelSession(tab.dataset.sid);
});
$("#qaNew").onclick = () => {
  qaNewSession();
  qaSaveTab(curTab());
  renderQA();
  $("#qaIn").focus();
};

async function qaSend() {
  if (qaBusy) return;
  const inp = $("#qaIn");
  const q = inp.value.trim();
  if (!q) return;
  if (!INFO || !INFO.loaded) {
    qaStatus("请先打开文档");
    return;
  }
  qaBusy = true;
  $("#qaSend").disabled = true;
  inp.value = "";
  addQaBubble("user", q);
  qaHist.push({ role: "user", content: q });
  const thinking = addQaBubble("assistant", "…");
  const list = $("#qaList");
  // 捕获发起时的标签/会话/历史数组: 回答回来时已切走则只写历史, 不污染当前视图
  const myTab = curTab();
  const myHist = qaHist;
  const mySess = qaSessions;
  const myDoc = INFO.doc;
  qaBusySid = qaSid;
  renderQaSessions();
  const live = () => curTab() === myTab && qaHist === myHist;
  try {
    // 流式(SSE over fetch): delta 事件逐块追加, 边收边渲染
    const r = await libPost("/api/ask-stream", {
      doc: myDoc, question: q, history: myHist.slice(0, -1),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "", answer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const di = block.indexOf("data: ");
        if (di < 0) continue;
        const data = JSON.parse(block.slice(di + 6));
        if (block.startsWith("event: delta")) {
          answer += data.text;
          if (live()) {   // 仍是发起标签的发起会话才更新气泡
            thinking.innerHTML = mdRender(answer);
            list.scrollTop = list.scrollHeight;
          }
        } else if (block.startsWith("event: failure")) {
          throw new Error(data.message || "请求失败");
        }
      }
    }
    if (!answer) throw new Error("空回答");
    myHist.push({ role: "assistant", content: answer });
    // 一问一答完整了才落库: 不必每个字都写
    qaPersist(mySess.find((x) => x.hist === myHist));
  } catch (err) {
    myHist.pop();   // 失败的问题不留在发起方的历史里
    if (live()) {
      thinking.textContent = "出错了：" + err;
      thinking.classList.add("err");
    }
  }
  if (myTab) myTab.qaSessions = mySess;
  qaBusy = false;
  qaBusySid = 0;
  $("#qaSend").disabled = false;
  renderQaSessions();   // 首问之后会话标题随之更新
  if (live()) list.scrollTop = list.scrollHeight;
}

$("#btnQA").onclick = toggleQA;
$("#qaSend").onclick = qaSend;

/* 问答模型: 独立于翻译模型, 按服务记忆; 选「默认」则与翻译模型一致 */
async function loadQaModel() {
  try {
    const d = await (await fetch("/api/settings")).json();
    const it = (d.items || []).find((x) => x.provider === d.current);
    if (!it) return;
    const sel = $("#qaModelSel"), inp = $("#qaModel");
    const models = it.models || [];
    const qa = it.qa_model || "";
    sel.innerHTML = `<option value="">默认: ${esc(it.custom_model || it.model)}</option>`
      + models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("")
      + `<option value="__custom__">自定义…</option>`;
    if (qa && !models.includes(qa)) {
      sel.value = "__custom__";
      inp.style.display = "";
      inp.value = qa;
    } else {
      sel.value = qa;
      inp.style.display = "none";
      inp.value = "";
    }
  } catch (e) { /* 设置读取失败时保持现状 */ }
}
async function saveQaModel() {
  const sel = $("#qaModelSel");
  const v = sel.value === "__custom__" ? $("#qaModel").value.trim() : sel.value;
  try {
    const r = await libPost("/api/settings", { qa_model: v });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    qaStatus("问答模型已保存");
  } catch (e) {
    qaStatus("模型保存失败：" + e, true);
  }
}
$("#qaModelSel").addEventListener("change", () => {
  const custom = $("#qaModelSel").value === "__custom__";
  const inp = $("#qaModel");
  inp.style.display = custom ? "" : "none";
  if (custom) { inp.focus(); return; }   // 等输入框 change 时再保存
  saveQaModel();
});
let qaModelSaveTimer = null;
$("#qaModel").addEventListener("change", () => {
  clearTimeout(qaModelSaveTimer);
  qaModelSaveTimer = setTimeout(saveQaModel, 300);
});
loadQaModel();

/* 速读摘要: 固定结构化 prompt, 结果服务端缓存 */
async function qaSummary() {
  if (qaBusy) return;
  if (!INFO || !INFO.loaded) {
    qaStatus("请先打开文档");
    return;
  }
  if (!document.body.classList.contains("qa")) toggleQA();
  qaBusy = true;
  $("#qaSend").disabled = true;
  $("#btnSum").disabled = true;
  addQaBubble("user", "生成速读摘要");
  const thinking = addQaBubble("assistant", "正在速读全文…");
  const myTab = curTab();   // 同 qaSend: 捕获发起方, 防跨标签/跨会话污染
  const myHist = qaHist;
  const mySess = qaSessions;
  qaBusySid = qaSid;
  renderQaSessions();
  const live = () => curTab() === myTab && qaHist === myHist;
  try {
    const r = await libPost("/api/summary", { doc: INFO.doc });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    if (live()) thinking.innerHTML = mdRender(d.answer);
    myHist.push({ role: "user", content: "生成速读摘要" },
                { role: "assistant", content: d.answer });
    qaPersist(mySess.find((x) => x.hist === myHist));
  } catch (err) {
    if (live()) {
      thinking.textContent = "出错了：" + err;
      thinking.classList.add("err");
    }
  }
  if (myTab) myTab.qaSessions = mySess;
  qaBusy = false;
  qaBusySid = 0;
  $("#qaSend").disabled = false;
  $("#btnSum").disabled = false;
  renderQaSessions();
}
$("#btnSum").onclick = qaSummary;
$("#qaIn").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); qaSend(); }
});

