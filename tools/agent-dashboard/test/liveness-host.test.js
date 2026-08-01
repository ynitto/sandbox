'use strict';

// project.js の稼働判定（projectLiveness）が、ローカル稼働を「別マシン」と誤認しないことを検証する。
// 追加依存なしで `node test/liveness-host.test.js` で走る。
//
// 背景: 本体（agent-project）の心拍は instances/*.json（ttl×3＝既定 270 秒）と status.json
// （fresh_after_sec＝既定 600 秒）の 2 系統で見ている。長いタスク（LLM 実行）に入ると心拍が
// 飛ばず instances 側が先に切れるため、270〜600 秒の間は status.json だけが生きている状態に
// なる。これを一律 status-sync（＝リモート本体を同期越しに見ている）と解釈していたため、
// サイドバーのプロジェクト名に `~` が付き、概要に「稼働中（別マシン）」と出ていた。
// status.json の host が自ホストなら別マシンではない。

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

// instances に載っていない（＝心拍が切れた）プロジェクトを作り、status.json だけを置く。
function projectWithStatus(status) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-live-'));
  fs.writeFileSync(path.join(tmp, 'status.json'), JSON.stringify(status));
  return tmp;
}

function isoAgo(sec) {
  const d = new Date(Date.now() - sec * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

test('自ホストが書いた status.json は status-local（別マシン扱いにしない）', () => {
  const dir = projectWithStatus({
    host: os.hostname(),
    watch: true,
    paused: false,
    updated_iso: isoAgo(300), // instances の窓（270秒）は過ぎ、status の窓（600秒）内
    fresh_after_sec: 600,
  });
  try {
    const live = project.projectLiveness(dir);
    assert.strictEqual(live.via, 'status-local');
    assert.strictEqual(live.running, true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('別ホストが書いた status.json は従来どおり status-sync（別マシン）', () => {
  const dir = projectWithStatus({
    // 別ホストは**前置**で作る。後置（`${os.hostname()}-other`）だと、ホスト名に
    // ドットが含まれる環境（macOS の `foo.local` 等）で短縮名が変わらず、hostsMatch が
    // 同一マシンと判定してしまう——DNS サフィックス差の吸収は本体の意図した挙動なので、
    // 別マシンを表したいテスト側が短縮名まで変える必要がある。
    host: `other-${os.hostname()}`,
    watch: true,
    paused: false,
    updated_iso: isoAgo(60),
    fresh_after_sec: 600,
  });
  try {
    const live = project.projectLiveness(dir);
    assert.strictEqual(live.via, 'status-sync');
    assert.strictEqual(live.running, true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('host が書かれていない status.json は判定材料が無いので従来どおり status-sync', () => {
  const dir = projectWithStatus({
    watch: true,
    paused: false,
    updated_iso: isoAgo(60),
    fresh_after_sec: 600,
  });
  try {
    const live = project.projectLiveness(dir);
    assert.strictEqual(live.via, 'status-sync');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('status.json が無ければ none（判定材料なし）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-live-'));
  try {
    const live = project.projectLiveness(tmp);
    assert.strictEqual(live.via, 'none');
    assert.strictEqual(live.running, false);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('実行エンジンが監督している子は children[].alive を確定根拠にする', () => {
  // 根拠を instances レジストリ（自己申告 + 心拍鮮度）から engine/status.json の
  // children[].alive（親が Popen で見た実測）へ移した（実装計画 W1-9）。心拍窓を持たないので、
  // 長い作業（LLM 実行）中に窓が切れて「停止中」と誤表示する経路が構造的に消える。
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-live-eng-'));
  fs.mkdirSync(path.join(tmp, 'backlog'), { recursive: true });
  try {
    const alive = { name: 'p1', alive: true, quarantined: false, paused: false, root: tmp };
    const live = project.projectLiveness(tmp, alive);
    assert.strictEqual(live.running, true, JSON.stringify(live));
    assert.strictEqual(live.via, 'engine');
    // 監督下で止まっている子は「稼働していない」と確定できる（推定へ落とさない）
    const dead = { name: 'p1', alive: false, quarantined: false, paused: false, root: tmp };
    assert.strictEqual(project.projectLiveness(tmp, dead).running, false);
    assert.strictEqual(project.projectLiveness(tmp, dead).via, 'engine');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// --- readNodeStatuses（案6・ノード別生存一覧） ---
test('readNodeStatuses は status/<node>.json を新しさ付きで並べる', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-nodes-'));
  try {
    const sdir = path.join(tmp, 'status');
    fs.mkdirSync(sdir, { recursive: true });
    const now = new Date().toISOString();
    const old = new Date(Date.now() - 9999 * 1000).toISOString();
    fs.writeFileSync(path.join(sdir, 'pc-a.json'), JSON.stringify(
      { node: 'pc-a', host: 'h1', updated_iso: now, fresh_after_sec: 120, watch: true, level: 'unattended' }));
    fs.writeFileSync(path.join(sdir, 'pc-b.json'), JSON.stringify(
      { node: 'pc-b', host: 'h2', updated_iso: old, fresh_after_sec: 120 }));
    const nodes = project.readNodeStatuses(tmp);
    assert.deepStrictEqual(nodes.map((n) => n.node), ['pc-a', 'pc-b']); // 名前順
    assert.strictEqual(nodes[0].running, true);   // 新しい → 稼働中
    assert.strictEqual(nodes[1].running, false);  // 古い → 応答なし（heartbeat 途絶）
    assert.ok(nodes[1].ageSec > 1000);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('readNodeStatuses は status/ が無ければ空配列', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-nodes-'));
  try {
    assert.deepStrictEqual(project.readNodeStatuses(tmp), []);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
