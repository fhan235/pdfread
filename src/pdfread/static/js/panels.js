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
/* 每个标签页各自保存一组对话会话(多窗口), qaHist 始终指向当前会话的历史数组;
   问答不走缓存, 每次都发送全文。同一时刻只允许一个请求在跑(qaBusy) */
let qaSessions = [];   // [{id, hist:[{role,content}]}]
let qaSid = 0;         // 当前会话 id
let qaSeq = 0;         // 会话 id 自增
let qaHist = [];       // === 当前会话的 hist (同一数组对象, 不可重新赋值成副本)
let qaBusy = false;
let qaBusySid = 0;     // 正在等待回答的会话, 用于在列表里标记 ●

/* 会话标题: 取首条用户提问, 没有则「新对话」 */
function qaSessTitle(s) {
  const first = (s.hist || []).find((m) => m.role === "user");
  const t = first ? first.content.replace(/\s+/g, " ").trim() : "";
  return t ? (t.length > 20 ? t.slice(0, 20) + "…" : t) : "新对话";
}

function qaCurSess() {
  return qaSessions.find((s) => s.id === qaSid);
}

/* 新建会话并切到它; 返回新会话 */
function qaNewSession() {
  const s = { id: ++qaSeq, hist: [] };
  qaSessions.push(s);
  qaSid = s.id;
  qaHist = s.hist;
  return s;
}

/* 从标签快照恢复会话组; 兼容旧快照里的单条 qaHist */
function qaLoadTab(t) {
  qaSessions = (t && t.qaSessions) || [];
  if (!qaSessions.length) {
    qaSessions = [{ id: ++qaSeq, hist: (t && t.qaHist) || [] }];
  }
  const s = qaSessions.find((x) => x.id === (t && t.qaSid)) || qaSessions[0];
  qaSid = s.id;
  qaHist = s.hist;
}

/* 写回标签快照 */
function qaSaveTab(t) {
  if (!t) return;
  t.qaSessions = qaSessions;
  t.qaSid = qaSid;
}

function renderQaSessions() {
  const sel = $("#qaSessSel");
  sel.innerHTML = qaSessions.map((s, i) =>
    `<option value="${s.id}">${s.id === qaBusySid ? "● " : ""}`
    + `${i + 1}. ${esc(qaSessTitle(s))}</option>`).join("");
  sel.value = String(qaSid);
  $("#qaDel").disabled = qaSessions.length === 1 && !qaHist.length;
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
   ``` 代码块、| 表格 |、> 引用、--- 分割线。
   容错: 表格行/列表项之间夹空行不断开(模型常这样输出); 代码块未闭合(流式中)渲染到末尾 */
const MD_UL = /^\s*[-*+•]\s+(.*)/;
const MD_OL = /^\s*(\d+)[.、)]\s+(.*)/;
const MD_HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
const MD_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

function mdRender(src) {
  // 行内代码先切出来, 其内容不再做粗斜体变换
  const inline = (s) => s.split(/(`[^`]+`)/).map((seg, k) => (k % 2
    ? `<code>${esc(seg.slice(1, -1))}</code>`
    : esc(seg)
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\*([^*]+)\*/g, "<i>$1</i>"))).join("");
  const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
  const nextNonBlank = (k) => {
    while (k < lines.length && !lines[k].trim()) k++;
    return k;
  };
  const listType = (l) => (MD_OL.test(l) ? "ol" : MD_UL.test(l) && !MD_HR.test(l) ? "ul" : null);
  const isSep = (l) => l.includes("|") && MD_SEP.test(l);
  const cells = (l) => {
    let s = l.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
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
  $("#qaHint").textContent = (INFO && INFO.loaded)
    ? `基于当前文档全文（约 ${(INFO.chars / 10000).toFixed(1)} 万字符），`
      + `每次提问都会将正文发送给翻译服务，超长只保留首尾`
    : "请先打开文档";
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
$("#qaSessSel").addEventListener("change", (e) => qaSwitchSession(e.target.value));
$("#qaNew").onclick = () => {
  qaNewSession();
  qaSaveTab(curTab());
  renderQA();
  $("#qaIn").focus();
};
$("#qaDel").onclick = () => {
  const s = qaCurSess();
  if (!s) return;
  if (s.hist.length && !confirm(`删除对话「${qaSessTitle(s)}」？\n该对话的问答记录会丢失。`)) return;
  const i = qaSessions.indexOf(s);
  qaSessions.splice(i, 1);
  if (!qaSessions.length) qaNewSession();
  else {
    const next = qaSessions[Math.min(i, qaSessions.length - 1)];
    qaSid = next.id;
    qaHist = next.hist;
  }
  qaSaveTab(curTab());
  renderQA();
};

async function qaSend() {
  if (qaBusy) return;
  const inp = $("#qaIn");
  const q = inp.value.trim();
  if (!q) return;
  if (!INFO || !INFO.loaded) {
    $("#qaHint").textContent = "请先打开文档";
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
    $("#qaHint").textContent = "问答模型已保存";
  } catch (e) {
    $("#qaHint").textContent = "模型保存失败：" + e;
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
    $("#qaHint").textContent = "请先打开文档";
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

