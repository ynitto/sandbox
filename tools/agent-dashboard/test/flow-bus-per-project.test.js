'use strict';

// project.js resolveBusDir のプロジェクト単位バス解決（flowBusByProject）を検証する軽量テスト。
// 本体の state_git_projects で agent-flow を分けている場合、pure-remote（clone のみ）でも
// プロジェクトごとの <clone>/agent-flow を採用できることを確認する。追加依存なし。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkbus(dir) {
  fs.mkdirSync(path.join(dir, 'runs'), { recursive: true });
  return dir;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-bus-'));

// <container>/projects/<name> レイアウト
const container = path.join(tmp, '.agent-projects');
const alphaDir = path.join(container, 'projects', 'alpha');
fs.mkdirSync(alphaDir, { recursive: true });

test('flowBusByProject[<name>] が pure-remote で採用される（ローカル bus なし）', () => {
  const busClone = mkbus(path.join(tmp, 'clone-alpha', 'agent-flow'));
  const cfg = { projects: { flowBusByProject: { alpha: busClone } } };
  const r = project.resolveBusDir(alphaDir, alphaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(busClone));
  assert.strictEqual(r.source, 'config-per-project');
});

test('明示バス設定があるときはローカル bus より設定側を優先する', () => {
  const busClone = mkbus(path.join(tmp, 'clone-alpha2', 'agent-flow'));
  mkbus(path.join(alphaDir, 'bus'));
  const cfg = { projects: { flowBusByProject: { alpha: busClone } } };
  const r = project.resolveBusDir(alphaDir, alphaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(busClone));
  assert.strictEqual(r.source, 'config-per-project');
});

test('写像に無いプロジェクトは従来どおり flowBus / 自動発見にフォールバック', () => {
  const betaDir = path.join(container, 'projects', 'beta');
  fs.mkdirSync(betaDir, { recursive: true });
  const shared = mkbus(path.join(tmp, 'shared', 'bus'));
  const cfg = { projects: { flowBusByProject: { alpha: '/nope' }, flowBus: shared } };
  const r = project.resolveBusDir(betaDir, betaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(shared));
  assert.strictEqual(r.source, 'config');
});

console.log(`\n${passed} passed`);
