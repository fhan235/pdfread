// No frontend build dependency: exercise real handlers with a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
// 前端按功能拆成多个经典脚本, 共享全局作用域, 加载顺序即依赖顺序
const JS_FILES = ['state', 'tabs', 'pages', 'library', 'highlight', 'links', 'panels', 'ui', 'prefs'];
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
