// No frontend build dependency: exercise real handlers with a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
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
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [], addEventListener() {},
      getElementById: id => element('#' + id), createElement: () => element('new') },
    window: { addEventListener() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    localStorage: { getItem() { return null; }, setItem() {} },
    setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
    setInterval() {}, clearInterval() {}, performance: { now: () => 0 },
    fetch: async () => ({ ok: false, json: async () => ({ detail: 'Invalid PDF' }) }),
    EventSource: function(url) { this.url = url; this.addEventListener = () => {}; },
    FormData: class { append() {} },
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
  run('document.querySelector("#qaDel").onclick()');
  assert.equal(run('qaSessions.length'), 1);
  assert.equal(run('qaHist[0].content'), '第二个问题');
});

test('math renders inline and as display blocks', () => {
  const { run } = setup();
  const md = '顶点 $v$ 的能量\n\n$$\n\n\\min_{v\\in\\text{voxel}} e(v)=\\sum_i d^2_{\\Pi,i}+\\lambda_{\\text{reg}}\\,d^2_{\\bar q}\n\n$$\n\n收尾';
  const html = run(`mdRender(${JSON.stringify(md)})`);
  assert.match(html, /<div class="md-math">/);      // $$ 单独成行也认
  assert.match(html, /∑/);
  assert.match(html, /λ/);
  assert.match(html, /mlim/);                       // 公式块里 \sum 的限标上下排
  assert.match(html, /class="mover"><i>q<\/i>/);    // \bar q 的上划线盖在 q 上, 不是空格
  assert.match(html, /mss/);                        // d^2_{\Pi,i} 上下标堆叠
  assert.doesNotMatch(html, /\\sum|\\lambda|\\text/); // 不残留裸 TeX
  assert.match(html, /<p>收尾<\/p>/);
});

test('math does not break table cells and spares currency', () => {
  const { run } = setup();
  const md = '| 符号 | 含义 |\n|---|---|\n| $\\lVert v\\rVert^2$ | 范数 |';
  const html = run(`mdRender(${JSON.stringify(md)})`);
  assert.equal((html.match(/<td/g) || []).length, 2);   // 公式里的内容没把单元格切坏
  assert.match(html, /‖/);
  // 金额不是公式: 不能被当成行内数学吃掉
  assert.match(run('mdRender("价格 $5 和 $10 之间")'), /<p>价格 \$5 和 \$10 之间<\/p>/);
  // 行内代码里的 $ 保持原样
  assert.match(run('mdRender("`$x^2$`")'), /<code>\$x\^2\$<\/code>/);
});

test('math renderer escapes html in every position', () => {
  const { run } = setup();
  const cases = ['$<script>alert(1)</script>$', '$\\text{<img src=x onerror=alert(1)>}$',
    '$x^{"><script>alert(1)</script>}$', '$\\begin{array}{"><script>bad</script>} a \\end{array}$'];
  const ok = new Set(['p', 'b', 'i', 'sub', 'sup', 'br', 'code', 'div', 'span']);
  for (const src of cases) {
    const html = run(`mdRender(${JSON.stringify(src)})`);
    for (const [, name, attr] of html.matchAll(/<\/?([a-zA-Z0-9]+)((?:\s+[a-zA-Z-]+="[^"<>]*")*)\s*>/g)) {
      assert.ok(ok.has(name.toLowerCase()), `意外标签 ${name}`);
      assert.doesNotMatch(attr, /\son[a-z]+=/i);
    }
    const text = html.replace(/<\/?[a-zA-Z0-9]+(?:\s+[a-zA-Z-]+="[^"<>]*")*\s*>/g, '');
    assert.ok(!text.includes('<') && !text.includes('>'), `残留未转义尖括号: ${text}`);
  }
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
