/* ---------------- 数学公式渲染(KaTeX, 本地自托管) ----------------
   改用 KaTeX: 排版基于 Knuth 的 TeX 度量, 行高/上下标/定界符缩放都由
   字体度量算出, 效果与 ChatGPT(同样是 KaTeX) 一致, 不再是手搓近似。
   资源全部放在 static/vendor/katex/(JS + CSS + woff2 字体), 不引任何 CDN,
   离线/内网环境照常可用 —— 这是本机应用, 不能依赖外网。

   安全: trust 保持 false, 因此 \href / \url / \includegraphics /
   \htmlClass / \htmlStyle / \htmlData 全部失效(只会以错误色显示原文),
   KaTeX 自身会转义所有文本内容, 输出标签集受限(span/svg/path + MathML)。
   maxExpand / maxSize 限制宏展开与尺寸, 防止构造输入耗死渲染。

   对外只暴露 texRender(src, display), 调用方(panels.js)不感知实现。 */

/* LLM 常用但 KaTeX 未内置的命令: 预定义成宏, 避免整条公式变成报错 */
const TEX_MACROS = {
  "\\argmax": "\\operatorname*{arg\\,max}",
  "\\argmin": "\\operatorname*{arg\\,min}",
  "\\R": "\\mathbb{R}", "\\N": "\\mathbb{N}", "\\Z": "\\mathbb{Z}",
  "\\Q": "\\mathbb{Q}", "\\C": "\\mathbb{C}", "\\E": "\\mathbb{E}",
  "\\Var": "\\operatorname{Var}", "\\Cov": "\\operatorname{Cov}",
  "\\diag": "\\operatorname{diag}", "\\tr": "\\operatorname{tr}",
  "\\rank": "\\operatorname{rank}", "\\sgn": "\\operatorname{sgn}",
  "\\softmax": "\\operatorname{softmax}",
  "\\sigmoid": "\\operatorname{sigmoid}",
  "\\KL": "\\operatorname{KL}", "\\dd": "\\mathrm{d}",
};

/* KaTeX 只渲染公式本身, 不认 LaTeX 文档级环境与编号命令;
   模型却经常连 \begin{equation} 和 \label{} 一起输出, 先剥掉再交给它。
   eqnarray 的 `a &=& b` 写法转成 aligned 的 `a &= b`。 */
const TEX_DOC_ENV = /\\(?:begin|end)\s*\{(?:equation|displaymath|math)\*?\}/g;
const TEX_EQNARRAY = /\\(begin|end)\s*\{eqnarray\*?\}/g;

function texNormalize(src) {
  let s = String(src)
    .replace(/\\label\s*\{[^{}]*\}/g, "")
    .replace(/\\(?:notag|nonumber)(?![a-zA-Z])/g, "")
    .replace(TEX_DOC_ENV, "");
  if (TEX_EQNARRAY.test(s)) {
    s = s.replace(TEX_EQNARRAY, "\\$1{aligned}")
         // &=& / &\le& 等三段式中间列: 去掉右侧多余的 &
         .replace(/&\s*(\\[a-zA-Z]+|[=<>≤≥])\s*&/g, "&$1 ");
  }
  return s.trim();
}

/* throwOnError:false —— 流式输出时公式常常只到一半($$\frac{a}{),
   此时按错误色显示原文, 等后续分片到达再渲染成功, 不能整块消失。
   strict:false —— 允许公式里出现中文等 Unicode 文本, 中文回答很常见。 */
const TEX_OPTS = {
  throwOnError: false,
  strict: false,
  trust: false,
  output: "htmlAndMathml",   // 带 MathML: 可被朗读, 复制得到原始 LaTeX
  maxSize: 40,
  maxExpand: 500,
};

/* 对外入口: display=true 为独立公式块 */
function texRender(src, display) {
  const tex = texNormalize(src);
  if (!tex) return "";
  const engine = typeof katex !== "undefined" ? katex : null;
  if (engine) {
    try {
      return engine.renderToString(tex, Object.assign({}, TEX_OPTS, {
        displayMode: !!display,
        // 每次传新副本: KaTeX 会把 \gdef 写回 macros 对象,
        // 共用一份会让一条公式的定义泄漏到后面的公式里
        macros: Object.assign({}, TEX_MACROS),
      }));
    } catch (e) { /* 落到下面的兜底, 不吞公式 */ }
  }
  return `<span class="mathraw">${esc(String(src))}</span>`;
}
