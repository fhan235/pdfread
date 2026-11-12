/* ---------------- 极简 TeX 子集渲染(自写, 无外部依赖) ----------------
   本机离线应用, 不引外部 CDN/字体, 因此不用 KaTeX/MathJax, 只按 LLM 回答里
   常见的那部分语法渲染:
     上下标(含 x^2_i 堆叠) · \frac/\dfrac/\binom · \sqrt(含 \sqrt[n]) ·
     希腊字母与常用数学符号 · \text/\mathbf/\mathbb 等字体 ·
     \bar/\hat/\vec/\overline 等重音 · \sum/\int/\lim/\min 等带限大算符 ·
     \left\right 定界符 · 间距命令 · matrix/pmatrix/bmatrix/vmatrix/cases/aligned 环境
   不认识的命令按名称原样排成正体, 不静默丢弃(宁可难看也别丢信息)。
   所有文本都经 esc() 转义后才拼进 HTML。 */

/* 符号表: 命令名 -> 字符 */
const TEX_SYM = {
  // 希腊字母
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε",
  varepsilon: "ε", zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ",
  iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ",
  pi: "π", varpi: "ϖ", rho: "ρ", varrho: "ϱ", sigma: "σ", varsigma: "ς",
  tau: "τ", upsilon: "υ", phi: "φ", varphi: "ϕ", chi: "χ", psi: "ψ",
  omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ",
  Pi: "Π", Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
  // 二元运算/关系
  cdot: "⋅", times: "×", div: "÷", pm: "±", mp: "∓", ast: "∗",
  star: "⋆", circ: "∘", bullet: "∙", oplus: "⊕", ominus: "⊖",
  otimes: "⊗", odot: "⊙", leq: "≤", le: "≤", geq: "≥", ge: "≥",
  neq: "≠", ne: "≠", equiv: "≡", approx: "≈", simeq: "≃", sim: "∼",
  cong: "≅", propto: "∝", ll: "≪", gg: "≫", prec: "≺", succ: "≻",
  // 集合/逻辑
  in: "∈", notin: "∉", ni: "∋", subset: "⊂", supset: "⊃",
  subseteq: "⊆", supseteq: "⊇", cup: "∪", cap: "∩", setminus: "∖",
  emptyset: "∅", varnothing: "∅", forall: "∀", exists: "∃",
  nexists: "∄", neg: "¬", lnot: "¬", land: "∧", wedge: "∧",
  lor: "∨", vee: "∨", therefore: "∴", because: "∵",
  // 箭头
  to: "→", rightarrow: "→", Rightarrow: "⇒", leftarrow: "←",
  Leftarrow: "⇐", leftrightarrow: "↔", Leftrightarrow: "⇔",
  mapsto: "↦", uparrow: "↑", downarrow: "↓", implies: "⟹",
  iff: "⟺", longrightarrow: "⟶", longleftarrow: "⟵",
  // 其他
  infty: "∞", partial: "∂", nabla: "∇", prime: "′", degree: "°",
  angle: "∠", perp: "⊥", parallel: "∥", triangle: "△", square: "□",
  ldots: "…", cdots: "⋯", dots: "…", vdots: "⋮", ddots: "⋱",
  hbar: "ℏ", ell: "ℓ", Re: "ℜ", Im: "ℑ", aleph: "ℵ", surd: "√",
  // 定界符
  lVert: "‖", rVert: "‖", Vert: "‖", lvert: "|", rvert: "|", vert: "|",
  langle: "⟨", rangle: "⟩", lfloor: "⌊", rfloor: "⌋",
  lceil: "⌈", rceil: "⌉", lbrace: "{", rbrace: "}",
  lbrack: "[", rbrack: "]", backslash: "\\",
};

/* 带上下限的大算符: display 模式下限标排在符号上下方 */
const TEX_BIG = {
  sum: "∑", prod: "∏", coprod: "∐", int: "∫", iint: "∬", iiint: "∭",
  oint: "∮", bigcup: "⋃", bigcap: "⋂", bigoplus: "⨁", bigotimes: "⨂",
  lim: "lim", limsup: "lim sup", liminf: "lim inf", max: "max",
  min: "min", sup: "sup", inf: "inf", arg: "arg", argmax: "arg max",
  argmin: "arg min", det: "det", gcd: "gcd", Pr: "Pr",
};

/* 不带上下限的函数名(排正体) */
const TEX_FN = ["sin", "cos", "tan", "cot", "sec", "csc", "arcsin",
  "arccos", "arctan", "sinh", "cosh", "tanh", "log", "ln", "lg", "exp",
  "deg", "dim", "ker", "hom", "mod", "bmod"];

/* 字体/正体命令 -> CSS 类(内容按纯文本处理, 不再做数学变形) */
const TEX_FONT = {
  text: "mtxt", textrm: "mtxt", textnormal: "mtxt", mathrm: "mtxt",
  operatorname: "mtxt", textsf: "msf", mathsf: "msf",
  texttt: "mtt", mathtt: "mtt", textbf: "mbf", mathbf: "mbf",
  boldsymbol: "mbf", bm: "mbf", textit: "mit", mathit: "mit",
  mathbb: "mbb", mathcal: "mcal", mathfrak: "mcal",
};

/* 重音: bar/overline 用上划线, 其余用上方标记 */
const TEX_ACC = { hat: "^", widehat: "^", tilde: "~", widetilde: "~",
  vec: "→", dot: "˙", ddot: "¨", check: "ˇ", breve: "˘", acute: "´",
  grave: "`" };

/* 间距命令 -> em 宽度 */
const TEX_SPACE = { ",": 0.17, ":": 0.22, ";": 0.28, " ": 0.25,
  enspace: 0.5, quad: 1, qquad: 2, "!": -0.17, negthinspace: -0.17 };

/* 环境 -> [左定界符, 右定界符] */
const TEX_ENV = {
  matrix: ["", ""], pmatrix: ["(", ")"], bmatrix: ["[", "]"],
  Bmatrix: ["{", "}"], vmatrix: ["|", "|"], Vmatrix: ["‖", "‖"],
  cases: ["{", ""], aligned: ["", ""], align: ["", ""],
  "align*": ["", ""], gathered: ["", ""], gather: ["", ""],
  "gather*": ["", ""], array: ["", ""], split: ["", ""],
};

/* 需要左右留空的二元运算/关系符 */
const TEX_BIN = new Set(["+", "−", "=", "<", ">", "±", "∓", "×", "÷",
  "≤", "≥", "≠", "≡", "≈", "≃", "∼", "≅", "∝", "≪", "≫", "∈", "∉",
  "⊂", "⊃", "⊆", "⊇", "∪", "∩", "→", "⇒", "←", "⇐", "↔", "⇔", "↦",
  "⟹", "⟺", "⟶", "⟵", "∧", "∨", "⊕", "⊖", "⊗", "±"]);

function texTokens(src) {
  const re = /\\[a-zA-Z]+\*?|\\[\s\S]|[{}^_&]|\s+|[\s\S]/g;
  const toks = [];
  let m;
  while ((m = re.exec(src))) {
    const s = m[0];
    if (s[0] === "\\") toks.push({ k: "cmd", v: s.slice(1) });
    else if (/^[{}^_&]$/.test(s)) toks.push({ k: s });
    else if (/^\s+$/.test(s)) toks.push({ k: "sp" });
    else toks.push({ k: "ch", v: s });
  }
  return toks;
}

function texSkipSp(toks, p) {
  while (toks[p.i] && toks[p.i].k === "sp") p.i++;
}

/* 读一个花括号组(或单个 token)的原始文本, 用于 \text 这类正体命令 */
function texRawGroup(toks, p) {
  texSkipSp(toks, p);
  const t = toks[p.i];
  if (!t) return "";
  if (t.k !== "{") {
    p.i++;
    return t.k === "sp" ? " " : (t.v || "");
  }
  p.i++;
  let s = "", depth = 1;
  while (p.i < toks.length) {
    const x = toks[p.i];
    if (x.k === "{") { depth++; s += "{"; p.i++; continue; }
    if (x.k === "}") {
      depth--; p.i++;
      if (!depth) break;
      s += "}"; continue;
    }
    s += x.k === "sp" ? " " : (x.v || "");
    p.i++;
  }
  return s;
}

/* 读一个花括号组(或单个原子)并渲染; 先跳过空白, 否则 \bar q 会把上划线画在空格上 */
function texGroup(toks, p, display) {
  texSkipSp(toks, p);
  const t = toks[p.i];
  if (!t) return "";
  if (t.k === "{") {
    p.i++;
    return texSeq(toks, p, display, true);
  }
  return texAtomScripted(toks, p, display);
}

/* 上下标的参数: {…} 取整组, 否则只取一个原子且不让它再吃后面的上下标
   —— d^2_{\Pi,i} 里的 2 不能把 _{\Pi,i} 抢走 */
function texScriptArg(toks, p, display) {
  texSkipSp(toks, p);
  const t = toks[p.i];
  if (!t) return "";
  if (t.k === "{") {
    p.i++;
    return texSeq(toks, p, display, true);
  }
  return texAtom(toks, p, display).h;
}

function texDelim(toks, p) {
  texSkipSp(toks, p);
  const t = toks[p.i];
  if (!t) return "";
  p.i++;
  if (t.k === "cmd") {
    if (t.v === ".") return "";                 // \left. 表示无定界符
    return TEX_SYM[t.v] || t.v;
  }
  if (t.k === "ch") return t.v === "." ? "" : t.v;
  if (t.k === "{" || t.k === "}") return t.k;
  return "";
}

/* 环境体: 取到配对的 \end{...} 为止(处理嵌套) */
function texEnvBody(toks, p) {
  const body = [];
  let depth = 0;
  while (p.i < toks.length) {
    const t = toks[p.i];
    if (t.k === "cmd" && t.v === "begin") depth++;
    if (t.k === "cmd" && t.v === "end") {
      if (!depth) {
        p.i++;
        texRawGroup(toks, p);   // 吃掉 {name}
        return body;
      }
      depth--;
    }
    body.push(t);
    p.i++;
  }
  return body;
}

function texEnv(toks, p, name, display) {
  const body = texEnvBody(toks, p);
  // 按 \\ 切行, 按 & 切列(只在花括号深度 0 处切)
  const rows = [[[]]];
  let depth = 0;
  for (const t of body) {
    if (t.k === "{") depth++;
    if (t.k === "}") depth--;
    if (!depth && t.k === "cmd" && (t.v === "\\" || t.v === "cr")) {
      rows.push([[]]);
      continue;
    }
    if (!depth && t.k === "&") {
      rows[rows.length - 1].push([]);
      continue;
    }
    const row = rows[rows.length - 1];
    row[row.length - 1].push(t);
  }
  const cols = Math.max(...rows.map((r) => r.length));
  const cellsHtml = rows.map((r) => {
    const tds = [];
    for (let c = 0; c < cols; c++) {
      const sub = r[c] || [];
      tds.push(`<span class="mcell">${texSeq(sub, { i: 0 }, display, false)}</span>`);
    }
    return tds.join("");
  }).join("");
  const [lb, rb] = TEX_ENV[name] || ["", ""];
  const grid = `<span class="menv" style="grid-template-columns:repeat(${cols},auto)">`
    + `${cellsHtml}</span>`;
  if (!lb && !rb) return grid;
  return `<span class="menvw">${lb ? `<span class="mdel">${esc(lb)}</span>` : ""}`
    + grid + (rb ? `<span class="mdel">${esc(rb)}</span>` : "") + `</span>`;
}

/* 渲染单个原子; 返回 {h, big}: big 标记带上下限的大算符 */
function texAtom(toks, p, display) {
  const t = toks[p.i];
  if (!t) return { h: "" };
  if (t.k === "ch") {
    p.i++;
    const c = t.v;
    if (/[a-zA-Z]/.test(c)) return { h: `<i>${esc(c)}</i>` };
    if (c === "-") return { h: `<span class="mbin">−</span>` };
    if (c === "'") return { h: "′" };
    if (TEX_BIN.has(c)) return { h: `<span class="mbin">${esc(c)}</span>` };
    return { h: esc(c) };
  }
  if (t.k === "{") return { h: texGroup(toks, p, display) };
  if (t.k === "}" || t.k === "&") { p.i++; return { h: "" }; }
  if (t.k === "sp") { p.i++; return { h: " " }; }
  if (t.k === "^" || t.k === "_") { p.i++; return { h: "" }; }

  // t.k === "cmd"
  const name = t.v;
  p.i++;
  if (name === "begin") {
    const env = texRawGroup(toks, p);
    if (env === "array") texRawGroup(toks, p);   // 吃掉列格式 {ccc}
    return { h: texEnv(toks, p, env, display) };
  }
  if (name === "end") { texRawGroup(toks, p); return { h: "" }; }
  if (name === "\\" || name === "cr") return { h: display ? "<br>" : " " };
  if (name === "left" || name === "right") {
    const d = texDelim(toks, p);
    return { h: d ? `<span class="mdel">${esc(d)}</span>` : "" };
  }
  if (name === "frac" || name === "dfrac" || name === "tfrac"
      || name === "binom" || name === "cfrac") {
    const a = texGroup(toks, p, false);
    const b = texGroup(toks, p, false);
    if (name === "binom") {
      return { h: `<span class="menvw"><span class="mdel">(</span>`
        + `<span class="mfrac nobar"><span class="num">${a}</span>`
        + `<span class="den">${b}</span></span>`
        + `<span class="mdel">)</span></span>` };
    }
    return { h: `<span class="mfrac"><span class="num">${a}</span>`
      + `<span class="den">${b}</span></span>` };
  }
  if (name === "sqrt") {
    let idx = "";
    if (toks[p.i] && toks[p.i].k === "ch" && toks[p.i].v === "[") {
      p.i++;
      let s = "";
      while (toks[p.i] && !(toks[p.i].k === "ch" && toks[p.i].v === "]")) {
        s += toks[p.i].v || "";
        p.i++;
      }
      p.i++;
      idx = `<span class="mroot">${esc(s)}</span>`;
    }
    const body = texGroup(toks, p, false);
    return { h: `${idx}<span class="msqrt"><span class="mrad">√</span>`
      + `<span class="mrbody">${body}</span></span>` };
  }
  if (TEX_FONT[name]) {
    const raw = texRawGroup(toks, p);
    return { h: `<span class="${TEX_FONT[name]}">${esc(raw)}</span>` };
  }
  if (name === "bar" || name === "overline") {
    const body = texGroup(toks, p, false);
    return { h: `<span class="mover">${body}</span>` };
  }
  if (name === "underline") {
    const body = texGroup(toks, p, false);
    return { h: `<span class="munder">${body}</span>` };
  }
  if (TEX_ACC[name]) {
    const body = texGroup(toks, p, false);
    return { h: `<span class="macc">${body}`
      + `<span class="mk">${esc(TEX_ACC[name])}</span></span>` };
  }
  if (Object.prototype.hasOwnProperty.call(TEX_SPACE, name)) {
    return { h: `<span style="margin-left:${TEX_SPACE[name]}em"></span>` };
  }
  if (TEX_BIG[name]) {
    return { h: `<span class="mop">${esc(TEX_BIG[name])}</span>`, big: true };
  }
  if (TEX_FN.includes(name)) {
    return { h: `<span class="mtxt">${esc(name)}</span>` };
  }
  if (TEX_SYM[name]) {
    const c = TEX_SYM[name];
    return { h: TEX_BIN.has(c)
      ? `<span class="mbin">${esc(c)}</span>` : esc(c) };
  }
  if (name.length === 1) return { h: esc(name) };   // \{ \% \$ 等转义字符
  if (name === "limits" || name === "nolimits"
      || name === "displaystyle" || name === "scriptstyle") return { h: "" };
  // 不认识的命令: 排成正体, 保留信息
  return { h: `<span class="mtxt munk">${esc(name)}</span>` };
}

function texAtomScripted(toks, p, display) {
  const base = texAtom(toks, p, display);
  let sub = "", sup = "";
  for (;;) {
    const t = toks[p.i];
    if (!t || (t.k !== "^" && t.k !== "_")) break;
    p.i++;
    const h = texScriptArg(toks, p, false);
    if (t.k === "^") sup += h;
    else sub += h;
  }
  if (!sub && !sup) return base.h;
  // 大算符在 display 下把限标排到上下方
  if (base.big && display) {
    return `<span class="mlim">`
      + (sup ? `<span class="mlimu">${sup}</span>` : "")
      + `<span class="mlims">${base.h}</span>`
      + (sub ? `<span class="mlimd">${sub}</span>` : "")
      + `</span>`;
  }
  if (sub && sup) {
    return `${base.h}<span class="mss"><span class="u">${sup}</span>`
      + `<span class="d">${sub}</span></span>`;
  }
  return base.h + (sup ? `<sup>${sup}</sup>` : `<sub>${sub}</sub>`);
}

function texSeq(toks, p, display, inBrace) {
  let out = "";
  while (p.i < toks.length) {
    const t = toks[p.i];
    if (t.k === "}") {
      p.i++;
      if (inBrace) return out;
      continue;
    }
    if (t.k === "cmd" && t.v === "end" && !inBrace) {
      // 环境结束符交回上层处理
      break;
    }
    out += texAtomScripted(toks, p, display);
  }
  return out;
}

/* 对外入口: display=true 为独立公式块 */
function texRender(src, display) {
  try {
    const html = texSeq(texTokens(String(src)), { i: 0 }, !!display, false);
    return `<span class="${display ? "mathd" : "mathi"}">${html}</span>`;
  } catch (e) {
    // 渲染失败不能吞掉公式: 退回转义后的原始 TeX
    return `<span class="mathraw">${esc(String(src))}</span>`;
  }
}
