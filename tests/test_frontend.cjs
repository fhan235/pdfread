// No frontend build dependency: exercise real handlers with a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
// 公式渲染交给本地自托管的 KaTeX, 与浏览器里加载的是同一份文件
const katex = require('../src/pdfread/static/vendor/katex/katex.min.js');
// 前端按功能拆成多个经典脚本, 共享全局作用域, 加载顺序即依赖顺序
const JS_FILES = ['state', 'tabs', 'pages', 'library', 'highlight', 'links', 'mathtex', 'panels', 'ui', 'prefs'];
const script = JS_FILES
  .map((f) => fs.readFileSync(`src/pdfread/static/js/${f}.js`, 'utf8'))
  .join('\n')
  .split('/* 启动:')[0];

function setup() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', innerHTML: '', textContent: '', disabled: false, style: {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      addEventListener() {}, querySelectorAll() { return []; }, appendChild() {},
      querySelector() { return null; }, remove() {},
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [], addEventListener() {},
      getElementById: id => element('#' + id), createElement: () => element('new'),
      body: { classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } } },
    window: { addEventListener() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    localStorage: { getItem() { return null; }, setItem() {} },
    setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
    setInterval() {}, clearInterval() {}, performance: { now: () => 0 },
    fetch: async () => ({ ok: false, json: async () => ({ detail: 'Invalid PDF' }) }),
    EventSource: function(url) { this.url = url; this.addEventListener = () => {}; },
    FormData: class { append() {} },
    katex,
  });
  vm.runInContext(script, context);
  return { context, element, run: code => vm.runInContext(code, context) };
}

test('invalid range retains existing translations', () => {
  const { element, run } = setup();
  run('INFO = {loaded:true,ready:true,pages:3,doc:"document-a"}');
  element('#pStart').value = '3';
  element('#pEnd').value = '1';
  element('#right').innerHTML = 'existing translation';
  run('run()');
  assert.equal(element('#right').innerHTML, 'existing translation');
  assert.match(element('#runStatus').textContent, /有效页码/);
});

test('opening invalid file restores previous content', async () => {
  const { element, run } = setup();
  element('#right').innerHTML = 'existing translation';
  await run('openFile("/example/bad.pdf")');
  assert.equal(element('#right').innerHTML, 'existing translation');
  assert.equal(element('#btnRun').disabled, false);
  assert.match(element('#runStatus').textContent, /打开失败/);
});

test('translation request identifies the tab document', () => {
  const { element, run } = setup();
  run('INFO = {loaded:true,ready:true,pages:3,doc:"document-a"}');
  element('#pStart').value = '1';
  element('#pEnd').value = '1';
  run('run()');
  assert.match(run('es.url'), /doc=document-a/);
});

test('markdown table renders even with blank lines between rows', () => {
  const { run } = setup();
  const md = '| 项 | 约束的方向 |\n\n|---|---|\n\n| a | **b** |\n\n之后的正文';
  const html = run(`mdRender(${JSON.stringify(md)})`);
  assert.match(html, /<table>/);
  assert.equal((html.match(/<tr>/g) || []).length, 2);
  assert.match(html, /<b>b<\/b>/);
  assert.match(html, /<p>之后的正文<\/p>/);   // 表后正文不被吞进表格
});

test('markdown renderer keeps code blocks verbatim and escapes html', () => {
  const { run } = setup();
  const html = run(`mdRender(${JSON.stringify('前言\n```\n| 纸 |\n<script>x</script>\n```')})`);
  assert.match(html, /<pre class="md-pre">/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test('qa sessions keep their histories separate', () => {
  const { context, run } = setup();
  context.confirm = () => true;
  run('tabs = [{id:"d1"}]; activeTab = "d1"; qaLoadTab(tabs[0]);');
  run('qaHist.push({role:"user",content:"第一个问题"})');
  run('qaNewSession(); qaHist.push({role:"user",content:"第二个问题"})');
  assert.equal(run('qaSessions.length'), 2);
  assert.equal(run('qaHist.length'), 1);
  run(`qaSwitchSession(${run('qaSessions[0].id')})`);
  assert.equal(run('qaHist[0].content'), '第一个问题');
  assert.equal(run('tabs[0].qaSessions.length'), 2);   // 会话组随标签快照保存
  // 关闭当前对话(标签上的 ×)后切到相邻一个
  run(`qaDelSession(${run('qaSessions[0].id')})`);
  assert.equal(run('qaSessions.length'), 1);
  assert.equal(run('qaHist[0].content'), '第二个问题');
});

test('qa tab bar renders one tab per session with close buttons', () => {
  const { context, element, run } = setup();
  context.confirm = () => true;
  run('tabs = [{id:"d1"}]; activeTab = "d1"; qaLoadTab(tabs[0]);');
  run('qaHist.push({role:"user",content:"向量检索是怎么做的"})');
  run('qaNewSession()');
  run('renderQaSessions()');
  const html = element('#qaTabs').innerHTML;
  assert.equal((html.match(/class="qtab/g) || []).length, 2);
  assert.match(html, /向量检索/);          // 标题取首条提问
  assert.match(html, /新对话/);            // 没提问过的显示占位名
  assert.match(html, /data-del=/);         // 每个标签自带关闭入口
  assert.match(html, /qtab on/);           // 当前会话高亮
  // 关闭非当前对话不该影响当前选中的会话
  const second = run('qaSid');
  run(`qaDelSession(${run('qaSessions[0].id')})`);
  assert.equal(run('qaSid'), second);
  assert.equal(run('qaSessions.length'), 1);
});

test('qa tab titles are escaped', () => {
  const { element, run } = setup();
  run('tabs = [{id:"d1"}]; activeTab = "d1"; qaLoadTab(tabs[0]);');
  run('qaHist.push({role:"user",content:"<img src=x onerror=alert(1)>"})');
  run('renderQaSessions()');
  const html = element('#qaTabs').innerHTML;
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

// KaTeX 的 MathML 里有一份 <annotation> 保留原始 LaTeX(用于朗读与复制),
// 断言"没有残留裸 TeX"时要先把它剥掉, 只看真正渲染出来的部分
const visibleOf = (html) => html.replace(/<annotation[^>]*>[\s\S]*?<\/annotation>/g, '');

test('math renders inline and as display blocks', () => {
  const { run } = setup();
  const md = '顶点 $v$ 的能量\n\n$$\n\n\\min_{v\\in\\text{voxel}} e(v)=\\sum_i d^2_{\\Pi,i}+\\lambda_{\\text{reg}}\\,d^2_{\\bar q}\n\n$$\n\n收尾';
  const html = run(`mdRender(${JSON.stringify(md)})`);
  assert.match(html, /<div class="md-math">/);      // $$ 单独成行也认
  assert.match(html, /class="katex-display"/);      // 公式块按 display 模式排版
  assert.match(html, /class="katex"/);              // 行内 $v$ 同样渲染
  assert.match(html, /∑/);
  assert.match(html, /λ/);
  assert.match(html, /<msubsup/);                   // d^2_{\Pi,i} 上下标堆叠
  assert.match(html, /accent/);                     // \bar q 的上划线
  assert.doesNotMatch(visibleOf(html), /\\sum|\\lambda|\\text/);  // 不残留裸 TeX
  assert.match(html, /<annotation encoding="application\/x-tex">/); // 可复制回 LaTeX
  assert.match(html, /<p>收尾<\/p>/);
});

test('math does not break table cells and spares currency', () => {
  const { run } = setup();
  const md = '| 符号 | 含义 |\n|---|---|\n| $\\lVert v\\rVert^2$ | 范数 |';
  const html = run(`mdRender(${JSON.stringify(md)})`);
  assert.equal((html.match(/<td/g) || []).length, 2);   // 公式里的内容没把单元格切坏
  assert.match(html, /∥/);                              // KaTeX 用 U+2225 排 \lVert
  // 金额不是公式: 不能被当成行内数学吃掉
  assert.match(run('mdRender("价格 $5 和 $10 之间")'), /<p>价格 \$5 和 \$10 之间<\/p>/);
  // 行内代码里的 $ 保持原样
  assert.match(run('mdRender("`$x^2$`")'), /<code>\$x\^2\$<\/code>/);
});

test('math tolerates latex document environments and partial streams', () => {
  const { run } = setup();
  // 模型常把 \begin{equation} 和 \label{} 一起输出, KaTeX 不认, 需先剥掉
  const eq = run(`mdRender(${JSON.stringify('$$\\begin{equation}\\label{eq:1} a^2+b^2=c^2 \\end{equation}$$')})`);
  assert.match(eq, /class="katex-display"/);
  assert.doesNotMatch(eq, /katex-error/);
  assert.doesNotMatch(visibleOf(eq), /equation|label/);
  // 流式输出到一半的公式不能让整块消失: 以原文+错误色呈现
  const half = run(`mdRender(${JSON.stringify('$$\\frac{a}{')})`);
  assert.match(half, /<div class="md-math">/);
  assert.match(half, /katex-error/);
  assert.match(half, /frac/);
  // LLM 常用但 KaTeX 没内置的命令由宏兜住, 不整条报错
  assert.doesNotMatch(run('mdRender("$\\\\argmax_x f(x)$")'), /katex-error/);
});

test('math renderer escapes html in every position', () => {
  const { run } = setup();
  const cases = ['$<script>alert(1)</script>$', '$\\text{<img src=x onerror=alert(1)>}$',
    '$x^{"><script>alert(1)</script>}$', '$\\begin{array}{"><script>bad</script>} a \\end{array}$',
    // trust 为 false: 这些命令不得产出链接/图片/内联样式注入
    '$\\href{javascript:alert(1)}{x}$', '$\\htmlStyle{background:url(x)}{y}$',
    '$\\includegraphics{x}$'];
  // Markdown 产出 + KaTeX 产出(span/svg/path 与 MathML)的标签全集
  const ok = new Set(['p', 'b', 'i', 'sub', 'sup', 'br', 'code', 'div', 'span',
    'svg', 'path', 'line', 'math', 'semantics', 'annotation', 'mrow', 'mi', 'mo',
    'mn', 'ms', 'mtext', 'mfrac', 'msqrt', 'mroot', 'msub', 'msup', 'msubsup',
    'mover', 'munder', 'munderover', 'mpadded', 'mphantom', 'mspace', 'mstyle',
    'menclose', 'mtable', 'mtr', 'mtd', 'merror']);
  for (const src of cases) {
    const html = run(`mdRender(${JSON.stringify(src)})`);
    for (const [, name, attr] of html.matchAll(/<\/?([a-zA-Z0-9]+)((?:\s+[a-zA-Z:-]+="[^"<>]*")*)\s*\/?>/g)) {
      assert.ok(ok.has(name.toLowerCase()), `意外标签 ${name}（来自 ${src}）`);
      assert.doesNotMatch(attr, /\son[a-z]+=/i);   // 不得出现事件处理器属性
    }
    const text = html.replace(/<\/?[a-zA-Z0-9]+(?:\s+[a-zA-Z:-]+="[^"<>]*")*\s*\/?>/g, '');
    assert.ok(!text.includes('<') && !text.includes('>'), `残留未转义尖括号: ${text}`);
    assert.doesNotMatch(html, /<a[\s>]|<img|<iframe|<script/i);   // 不生成可导航/可加载元素
  }
});

test('bold pairs across inline math and code', () => {
  const { run } = setup();
  // 模型很爱这样写; 旧实现先按公式 split 再逐段套正则,
  // ** 的开闭落在公式两侧的不同片段里, 永远配不上对 -> 原样显示 **
  const html = run(`mdRender(${JSON.stringify('**$d^2_{\\Pi,i}$：点到切平面的距离**')})`);
  assert.match(html, /<b>/);
  assert.match(html, /class="katex"/);
  assert.doesNotMatch(visibleOf(html), /\*\*/);   // 不残留星号
  // 一个粗体里跨多个公式
  const multi = run(`mdRender(${JSON.stringify('**$\\lambda_{bound}$、$\\lambda_{reg}$**：权重')})`);
  assert.match(multi, /<b>/);
  assert.equal((multi.match(/class="katex"/g) || []).length, 2);
  // 粗体包行内代码同理
  assert.match(run(`mdRender(${JSON.stringify('**调用 `run()` 之后**')})`), /<b>.*<code>/);
  // 乘号不能被当成斜体(旧的 /\*([^*]+)\*/ 会误伤)
  assert.doesNotMatch(run('mdRender("计算 3 * 4 * 5 的结果")'), /<i>/);
});

test('library list rows stay compact and sorting works', () => {
  const { element, run } = setup();
  run(`LIB = {folders:[{id:1,name:"方向一"}], tags:[], documents:[
    {id:1,path:"/a",name:"a.pdf",title:"Zeta 最后",folder_id:null,added:100,
     last_opened:300,highlights:2,tags:["综述"],progress:27,pages:28,exists:true},
    {id:2,path:"/b",name:"b.pdf",title:"Alpha 最先",folder_id:1,added:300,
     last_opened:100,highlights:0,tags:[],progress:3,pages:28,exists:false}
  ]}`);
  run('prefs.libView = "list"; prefs.libSort = "opened"; renderLibrary()');
  const html = element('#libList').innerHTML;
  assert.match(html, /class="doc dense/);        // 列表行
  assert.doesNotMatch(html, /data-move=/);       // 文件夹下拉不再每行一个
  assert.match(html, /data-ck="1"/);             // 批量勾选框
  assert.match(html, /96%|96/);                  // 进度百分比
  assert.match(html, /warn2/);                   // 文件失效标记
  assert.equal(element('#libCount').textContent, '2 篇');

  // 排序: 四种都按各自字段取首项
  const first = () => run('LIB.documents.slice().sort(LIB_SORTS[prefs.libSort].fn)[0].title');
  assert.equal(first(), 'Zeta 最后');
  run('prefs.libSort = "added"');
  assert.equal(first(), 'Alpha 最先');
  run('prefs.libSort = "title"');
  assert.equal(first(), 'Alpha 最先');
  run('prefs.libSort = "progress"');
  assert.equal(first(), 'Zeta 最后');
});

test('library bulk selection drops rows that left the filter', () => {
  const { element, run } = setup();
  run(`LIB = {folders:[{id:1,name:"方向一"}], tags:[], documents:[
    {id:1,path:"/a",name:"a.pdf",title:"甲",folder_id:1,added:1,last_opened:3,
     highlights:0,tags:[],progress:0,pages:10,exists:true},
    {id:2,path:"/b",name:"b.pdf",title:"乙",folder_id:null,added:2,last_opened:2,
     highlights:0,tags:[],progress:0,pages:10,exists:true}
  ]}`);
  run('prefs.libView = "list"; prefs.libSort = "opened"');
  run('libSel.add(1); libSel.add(2); renderLibrary()');
  assert.equal(run('libSel.size'), 2);
  assert.match(element('#libBulk').innerHTML, /已选 2 篇/);
  // 切到只含文档 1 的文件夹: 不可见的选中项被清理, 不会误操作到看不见的文档
  run('libScope = {kind:"folder", id:1, name:""}; renderLibrary()');
  assert.equal(run('libSel.size'), 1);
  assert.ok(run('libSel.has(1)'));
});

test('library titles and tags are escaped in list rows', () => {
  const { element, run } = setup();
  run(`LIB = {folders:[], tags:[], documents:[
    {id:1,path:"/<x>",name:"n.pdf",title:"<img src=x onerror=alert(1)>",
     folder_id:null,added:1,last_opened:1,highlights:0,
     tags:["<script>bad</script>"],progress:0,pages:0,exists:true}
  ]}`);
  run('prefs.libView = "list"; prefs.libSort = "opened"; renderLibrary()');
  const html = element('#libList').innerHTML;
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
});

test('library shelf renders cards with covers', () => {
  const { element, run } = setup();
  run(`LIB = {folders:[], tags:[], documents:[
    {id:7,path:"/a",name:"a.pdf",title:"有封面的",folder_id:null,added:1,
     last_opened:3,highlights:2,tags:["综述"],progress:14,pages:28,exists:true},
    {id:8,path:"/b",name:"b.epub",title:"文件丢了的",folder_id:null,added:2,
     last_opened:2,highlights:0,tags:[],progress:0,pages:0,exists:false}
  ]}`);
  // 书架由 body.shelf 决定, 与侧栏的 列表/详情 无关
  run('document.body.classList.contains = (c) => c === "shelf"');
  run('prefs.libView = "list"; prefs.libSort = "opened"; renderLibrary()');
  const html = element('#libList').innerHTML;
  assert.match(html, /class="card/);
  assert.doesNotMatch(html, /class="doc dense/);        // 书架不走列表行
  assert.match(html, /\/api\/thumb\/7\.png/);           // 存在的文档请求封面
  assert.match(html, /loading="lazy"/);                 // 懒加载, 不一次性拉完
  assert.doesNotMatch(html, /\/api\/thumb\/8\.png/);    // 文件已丢失的不请求
  assert.match(html, /class="ph">EPUB</);               // 退回扩展名占位
  assert.match(html, /width:50%/);                      // 14/28 的进度条
});

test('export excludes failed paragraphs and labels partial output', () => {
  const { context, element, run } = setup();
  let blobParts;
  context.Blob = class { constructor(parts) { blobParts = parts; } };
  context.URL = { createObjectURL: () => 'blob:test', revokeObjectURL() {} };
  context.document.body = { appendChild() {} };
  context.document.createElement = () => ({ click() {}, remove() {} });
  const para = (text, failed) => ({ classList: { contains: () => failed }, querySelector: () => ({textContent:text}) });
  const good = para('translated paragraph', false), bad = para('sensitive error details', true);
  const pg = {id:'R1', querySelectorAll: () => [good, bad]};
  element('#right').querySelectorAll = selector => ({
    '.pg .para': [good, bad], '.para.err': [bad], '.skeleton': [{textContent:'本页未完成，请重试'}], '.pg': [pg]
  })[selector] || [];
  run('INFO = {name:"paper"}; doExport()');
  assert.match(blobParts[0], /此导出不完整/);
  assert.match(blobParts[0], /translated paragraph/);
  assert.doesNotMatch(blobParts[0], /sensitive error details/);
});

test('link hot spots are positioned in percent and drop malformed entries', () => {
  const { run } = setup();
  const html = run(`linkHTML([
    [61.2, 79.2, 122.4, 158.4, 3, 396],
    [10, 10, 5, 20, 2, 100],
    [10, 10, 20, 20, "x", 100],
    [10, 10, 20, 20, 2, "<b>"],
  ], 612, 792)`);
  assert.equal((html.match(/class="plink"/g) || []).length, 1);
  assert.match(html, /data-tp="3" data-ty="396" data-tv="396"/);   // 旧格式无 tv: 同锚点
  assert.match(run('linkHTML([[1,1,9,9,2,400,120]], 612, 792)'), /data-ty="400" data-tv="120"/);
  assert.match(html, /left:10\.000%;top:10\.000%;width:10\.000%;height:10\.000%/);
  assert.doesNotMatch(html, /<b>/);
});

test('jumping to a reference and going back restores the reading position', () => {
  const { element, run } = setup();
  // 两页, 每页 1000px, 页面图与 .pg 等高; 左栏视口 800px
  const pane = element('#left');
  pane.scrollTop = 250;
  pane.getBoundingClientRect = () => ({ top: 0, bottom: 800, left: 0, width: 500, height: 800 });
  const pages = [1, 2].map((n) => {
    const el = element('#L' + n);
    el.id = 'L' + n;
    el.offsetHeight = 1000;
    el.getBoundingClientRect = () => {
      const top = (n - 1) * 1000 - pane.scrollTop;
      return { top, bottom: top + 1000, height: 1000 };
    };
    el.querySelector = (sel) => (sel === '.imgwrap' ? el : element(`#L${n} ${sel}`));
    return el;
  });
  pane.children = pages;
  for (const n of [1, 2]) {   // 译文栏没有内容
    element('#R' + n).getBoundingClientRect = () => ({ top: 0, bottom: 0, height: 0 });
  }
  element('#right').getBoundingClientRect = () => ({ top: 0, bottom: 800 });
  element('#right').children = [];
  run('INFO = {doc:"d1", fmt:"pdf", pages:2, sizes:[[612,792],[612,792]]}');

  run('jumpToLink(2, 396)');   // 第 2 页中部, 上方留 48px 余量
  assert.equal(pane.scrollTop, 1000 + 500 - 48);
  assert.equal(run('navBack.d1.length'), 1);
  assert.equal(element('#linkBack').style.display, 'block');

  run('navBackGo()');
  assert.equal(pane.scrollTop, 250);
  assert.equal(run('navBack.d1.length'), 0);
  assert.equal(element('#linkBack').style.display, 'none');

  // 图片链接: 滚到图顶(view), 高亮仍在图注(锚点)
  let mark = null;
  element('#L2 .llayer').appendChild = (el) => { mark = el; };
  run('jumpToLink(2, 594, 198)');   // 图注在 3/4 处, 图顶在 1/4 处
  assert.equal(pane.scrollTop, 1000 + 250 - 48);
  assert.equal(mark.style.top, '75.000%');
});
