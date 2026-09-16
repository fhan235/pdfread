// No frontend build dependency: exercise real handlers with a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const html = fs.readFileSync('src/pdfread/static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].split('/* 启动:')[0];

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
    setTimeout() {}, requestAnimationFrame() {},
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
