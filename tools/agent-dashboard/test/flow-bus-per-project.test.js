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

test('設定なしのリモート clone は <clone>/agent-flow（状態鏡写し）を自動発見する', () => {
  // 別 PC で実行中の run は agent-flow daemon の state-git が <clone>/agent-flow に鏡写しする。
  // 従来は flowBusByProject の手動設定が無いと見えなかった（＝リモート run が反映されない）。
  const gammaDir = path.join(container, 'projects', 'gamma');
  fs.mkdirSync(gammaDir, { recursive: true });
  mkbus(path.join(gammaDir, 'agent-flow'));       // ローカル bus は無く、鏡写しだけ在る
  const r = project.resolveBusDir(gammaDir, gammaDir, {});
  assert.strictEqual(r.hasBus, true);
  assert.strictEqual(r.busDir, path.resolve(path.join(gammaDir, 'agent-flow')));
  assert.strictEqual(r.source, 'state-mirror');
});

test('ローカル bus と鏡写しが両方在るときは新しい方（実測鮮度）を採る', () => {
  const deltaDir = path.join(container, 'projects', 'delta');
  fs.mkdirSync(deltaDir, { recursive: true });
  const localBus = mkbus(path.join(deltaDir, 'bus'));
  const mirror = mkbus(path.join(deltaDir, 'agent-flow'));
  const stamp = (dir, iso) => {
    const t = new Date(iso);
    for (const name of ['', ...fs.readdirSync(path.join(dir, 'runs'))]) {
      fs.utimesSync(name ? path.join(dir, 'runs', name) : path.join(dir, 'runs'), t, t);
    }
  };
  // 鏡写し側を新しく（別 PC の実行が同期で届いた状況）: 両バスの runs と全子を明示的に時刻付け。
  fs.writeFileSync(path.join(localBus, 'runs', 'r-old'), 'x');
  fs.mkdirSync(path.join(mirror, 'runs', 'r-new'), { recursive: true });
  stamp(localBus, '2026-07-20T00:00:00Z');
  stamp(mirror, '2026-07-21T00:00:00Z');
  assert.strictEqual(project.resolveBusDir(deltaDir, deltaDir, {}).busDir,
                     path.resolve(mirror), '新しい鏡写しを採用');
  // 逆にローカルが新しければローカルを採る
  fs.mkdirSync(path.join(localBus, 'runs', 'r-newer'), { recursive: true });
  stamp(mirror, '2026-07-19T00:00:00Z');
  stamp(localBus, '2026-07-22T00:00:00Z');
  assert.strictEqual(project.resolveBusDir(deltaDir, deltaDir, {}).busDir,
                     path.resolve(localBus), '新しいローカル bus を採用');
});

console.log(`\n${passed} passed`);
