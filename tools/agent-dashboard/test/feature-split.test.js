'use strict';

// 制御面分離（base / agent-project / kiro-loop / cowork）の配線テスト。
// Electron は起動せず、feature 列挙・preload 合成・互換シムだけを検証する。

const assert = require('assert');
const path = require('path');
const fs = require('fs');

const { loadFeatures } = require('../src/features');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('features に agent-project / kiro-loop / cowork が並ぶ', () => {
  const features = loadFeatures();
  const ids = features.map((f) => f.id);
  assert.deepStrictEqual(ids, ['agent-project', 'kiro-loop', 'cowork']);
});

test('各 feature が registerIpc / preloadApi / configDefaults を持つ', () => {
  for (const f of loadFeatures()) {
    assert.strictEqual(typeof f.registerIpc, 'function', `${f.id}.registerIpc`);
    assert.strictEqual(typeof f.preloadApi, 'function', `${f.id}.preloadApi`);
    assert.ok(f.configDefaults && typeof f.configDefaults === 'object', `${f.id}.configDefaults`);
  }
});

test('agent-project の設定既定に projects / agent がある', () => {
  const stack = loadFeatures().find((f) => f.id === 'agent-project');
  assert.ok(stack.configDefaults.projects);
  assert.ok(stack.configDefaults.agent);
  assert.strictEqual(stack.configDefaults.projects.command, 'agent-project');
});

test('agent-project preload に discover / flowRuns がある', () => {
  const stack = loadFeatures().find((f) => f.id === 'agent-project');
  const api = stack.preloadApi();
  assert.strictEqual(typeof api.discover, 'function');
  assert.strictEqual(typeof api.flowRuns, 'function');
  const calls = [];
  const discover = api.discover((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(discover(), 'ok');
  assert.deepStrictEqual(calls, [['dashboard:discover', undefined]]);
});

test('kiro-loop は tmux 視聴 API を登録する', () => {
  const loop = loadFeatures().find((f) => f.id === 'kiro-loop');
  const registered = [];
  loop.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(), ['kiroLoop:capture', 'kiroLoop:listSessions'].sort());
  const api = loop.preloadApi();
  assert.strictEqual(typeof api.kiroLoopListSessions, 'function');
  assert.strictEqual(typeof api.kiroLoopCapture, 'function');
  assert.ok(loop.configDefaults.kiroLoop);
});


test('cowork は定期実行と定型業務 API を登録する', () => {
  const cowork = loadFeatures().find((f) => f.id === 'cowork');
  assert.ok(cowork.configDefaults.cowork);
  assert.strictEqual(cowork.configDefaults.cowork.loopProvider, 'kiro-loop');
  assert.strictEqual(cowork.configDefaults.cowork.nextLoopProvider, 'agent-loop');
  const api = cowork.preloadApi();
  assert.strictEqual(typeof api.coworkOverview, 'function');
  assert.strictEqual(typeof api.coworkSaveWork, 'function');
  const calls = [];
  const overview = api.coworkOverview((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(overview(), 'ok');
  assert.deepStrictEqual(calls, [['cowork:overview', {}]]);
  assert.strictEqual(overview({ probeProcess: true }), 'ok');
  assert.deepStrictEqual(calls[1], ['cowork:overview', { probeProcess: true }]);
  assert.deepStrictEqual(cowork.configDefaults.cowork.items, []);
});

test('互換シム src/main/project.js が実体へ届く', () => {
  const viaShim = require('../src/main/project');
  const viaReal = require('../src/features/agent-project/main/project');
  assert.strictEqual(viaShim, viaReal);
  assert.strictEqual(typeof viaShim.discover, 'function');
});

test('base / feature の入口ファイルが存在する', () => {
  const root = path.join(__dirname, '..', 'src');
  for (const rel of [
    'base/main/main.js',
    'base/main/ipc.js',
    'base/main/config.js',
    'features/index.js',
    'features/agent-project/index.js',
    'features/kiro-loop/index.js',
    'features/kiro-loop/README.md',
    'features/cowork/index.js',
    'features/cowork/README.md',
  ]) {
    assert.ok(fs.existsSync(path.join(root, rel)), rel);
  }
});

test('HTML に data-feature マーカーがある', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.ok(html.includes('data-feature="agent-project"'));
  assert.ok(html.includes('data-feature="kiro-loop"'));
  assert.ok(html.includes('data-feature="cowork"'));
  assert.ok(html.includes('tab-cowork'));
});

console.log(`\n${passed} tests passed`);
