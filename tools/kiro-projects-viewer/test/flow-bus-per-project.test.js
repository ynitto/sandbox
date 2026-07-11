'use strict';

// kiro.js resolveBusDir のプロジェクト単位バス解決（flowBusByProject）を検証する軽量テスト。
// 本体の state_git_projects で kiro-flow を分けている場合、pure-remote（clone のみ）でも
// プロジェクトごとの <clone>/kiro-flow を採用できることを確認する。追加依存なし。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const kiro = require('../src/main/kiro');

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
const container = path.join(tmp, '.kiro-projects');
const alphaDir = path.join(container, 'projects', 'alpha');
fs.mkdirSync(alphaDir, { recursive: true });

test('flowBusByProject[<name>] が pure-remote で採用される（ローカル bus なし）', () => {
  const busClone = mkbus(path.join(tmp, 'clone-alpha', 'kiro-flow'));
  const cfg = { kiro: { flowBusByProject: { alpha: busClone } } };
  const r = kiro.resolveBusDir(alphaDir, alphaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(busClone));
  assert.strictEqual(r.source, 'config-per-project');
});

test('ローカル <project>/bus に runs があればそちらが優先される', () => {
  const busClone = mkbus(path.join(tmp, 'clone-alpha2', 'kiro-flow'));
  mkbus(path.join(alphaDir, 'bus'));
  const cfg = { kiro: { flowBusByProject: { alpha: busClone } } };
  const r = kiro.resolveBusDir(alphaDir, alphaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(path.join(alphaDir, 'bus')));
  assert.strictEqual(r.source, 'project');
});

test('写像に無いプロジェクトは従来どおり flowBus / 自動発見にフォールバック', () => {
  const betaDir = path.join(container, 'projects', 'beta');
  fs.mkdirSync(betaDir, { recursive: true });
  const shared = mkbus(path.join(tmp, 'shared', 'bus'));
  const cfg = { kiro: { flowBusByProject: { alpha: '/nope' }, flowBus: shared } };
  const r = kiro.resolveBusDir(betaDir, betaDir, cfg);
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(shared));
  assert.strictEqual(r.source, 'config');
});

console.log(`\n${passed} passed`);
