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

/* ---------------- AI 问答 ---------------- */
/* 每个标签页各自保存一份对话历史; 问答不走缓存, 每次都发送全文 */
let qaHist = [];
let qaBusy = false;

function toggleQA() {
  const on = !document.body.classList.contains("qa");
  document.body.classList.toggle("qa", on);
  $("#btnQA").classList.toggle("on", on);
  prefs.qa = on;
  savePrefs();
  if (on) { renderQA(); loadQaModel(); $("#qaIn").focus(); }
}

/* 极简 Markdown 渲染(先转义再变换, 防注入):
   支持 #/##/### 标题、**粗体**、*斜体*、`行内代码`、无序/有序列表 */
function mdRender(src) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>");
  const out = [];
  let list = null;
  const closeList = () => {
    if (list) { out.push(list === "ul" ? "</ul>" : "</ol>"); list = null; }
  };
  for (const raw of src.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { closeList(); continue; }
    if ((m = line.match(/^###\s+(.*)/))) {
      closeList(); out.push(`<div class="md-h4">${inline(m[1])}</div>`);
    } else if ((m = line.match(/^#{1,2}\s+(.*)/))) {
      closeList(); out.push(`<div class="md-h3">${inline(m[1])}</div>`);
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*(\d+)[.、]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[2])}</li>`);
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
  const box = $("#qaList");
  box.innerHTML = "";
  for (const m of qaHist) addQaBubble(m.role, m.content);
  $("#qaHint").textContent = (INFO && INFO.loaded)
    ? `基于当前文档全文（约 ${(INFO.chars / 10000).toFixed(1)} 万字符），`
      + `每次提问都会将正文发送给翻译服务，超长只保留首尾`
    : "请先打开文档";
}

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
  try {
    // 流式(SSE over fetch): delta 事件逐块追加, 边收边渲染
    const r = await libPost("/api/ask-stream", {
      doc: INFO.doc, question: q, history: qaHist.slice(0, -1),
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
          thinking.innerHTML = mdRender(answer);
          list.scrollTop = list.scrollHeight;
        } else if (block.startsWith("event: failure")) {
          throw new Error(data.message || "请求失败");
        }
      }
    }
    if (!answer) throw new Error("空回答");
    qaHist.push({ role: "assistant", content: answer });
  } catch (err) {
    thinking.textContent = "出错了：" + err;
    thinking.classList.add("err");
    qaHist.pop();   // 失败的问题不留在历史里
  }
  const t = curTab();
  if (t) t.qaHist = qaHist;
  qaBusy = false;
  $("#qaSend").disabled = false;
  list.scrollTop = list.scrollHeight;
}

$("#btnQA").onclick = toggleQA;
$("#qaSend").onclick = qaSend;

/* 问答模型: 独立于翻译模型, 按服务记忆; 留空则与翻译模型一致 */
async function loadQaModel() {
  try {
    const d = await (await fetch("/api/settings")).json();
    const it = (d.items || []).find((x) => x.provider === d.current);
    if (!it) return;
    const inp = $("#qaModel");
    inp.value = it.qa_model || "";
    inp.placeholder = "默认: " + (it.custom_model || it.model);
    $("#qaModelList").innerHTML = (it.models || [])
      .map((m) => `<option value="${esc(m)}">`).join("");
  } catch (e) { /* 设置读取失败时保持现状 */ }
}
let qaModelSaveTimer = null;
$("#qaModel").addEventListener("change", () => {
  clearTimeout(qaModelSaveTimer);
  qaModelSaveTimer = setTimeout(async () => {
    try {
      const r = await libPost("/api/settings", { qa_model: $("#qaModel").value.trim() });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      $("#qaHint").textContent = "问答模型已保存";
    } catch (e) {
      $("#qaHint").textContent = "模型保存失败：" + e;
    }
  }, 300);
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
  try {
    const r = await libPost("/api/summary", { doc: INFO.doc });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const d = await r.json();
    thinking.innerHTML = mdRender(d.answer);
    qaHist.push({ role: "user", content: "生成速读摘要" },
                { role: "assistant", content: d.answer });
  } catch (err) {
    thinking.textContent = "出错了：" + err;
    thinking.classList.add("err");
  }
  const t = curTab();
  if (t) t.qaHist = qaHist;
  qaBusy = false;
  $("#qaSend").disabled = false;
  $("#btnSum").disabled = false;
}
$("#btnSum").onclick = qaSummary;
$("#qaIn").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); qaSend(); }
});

