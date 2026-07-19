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

test('状態 worktree 構成でも instances を実効パス（backlog の親）で照合する', () => {
  // レコードの root は「リダイレクト前の素の root」で、viewer の登録パス（状態 worktree の
  // 実体）とは一致しない。実効パス（backlog の親）で照合しないと、稼働中でも instances を
  // 取りこぼし、長い作業中に「本体が停止中」と誤表示する（実際に起きた）。
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-live-wt-'));
  const idir = path.join(os.homedir(), '.agent-project', 'instances');
  fs.mkdirSync(idir, { recursive: true });
  const file = path.join(idir, `kpv-test-${process.pid}.json`);
  fs.writeFileSync(file, JSON.stringify({
    pid: process.pid,
    root: '/somewhere/else/.agent-project',          // リダイレクト前の root（一致しない）
    backlog: path.join(tmp, 'backlog'),             // 実効パス（実書き込み先）は一致する
    heartbeat: Date.now() / 1000,
    ttl: 90,
    host: os.hostname(),
  }));
  try {
    const live = project.projectLiveness(tmp);
    assert.strictEqual(live.running, true, JSON.stringify(live));
    assert.strictEqual(live.via, 'instances');
  } finally {
    fs.unlinkSync(file);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
