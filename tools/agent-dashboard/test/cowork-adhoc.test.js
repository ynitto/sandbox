'use strict';

// アドホック起動（M2）: 登録フォルダ + 自由文で対話 CLI を起動する経路のテスト。
//   ・cwd は登録済みフォルダからの選択のみ（未登録は拒否）
//   ・共通指示（agent-instructions）がプロンプト先頭へ前置される
//   ・履歴が historyFile（cowork-history.jsonl 相当）へ type:'adhoc' で残る
// 起動そのものは cowork.test.js と同じく platform=win32 を強制し、WSL 検査を
// 差し替えて「組み立てられたスクリプト」を検証する（実機の WSL には依存しない）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 走査ルートに必ず入るユーザーホームをスタブへ差し替える（実機の持ち物で結果を変えない）
const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'home-stub-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

const wslMain = require('../src/base/main/wsl');
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

const cowork = require('../src/features/cowork/main/cowork');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function makeConfig() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-adhoc-'));
  const historyFile = path.join(repo, 'history.jsonl');
  const instructionsDir = fs.mkdtempSync(path.join(os.tmpdir(), 'adhoc-instr-'));
  fs.writeFileSync(path.join(instructionsDir, 'instructions.json'), JSON.stringify({
    version: 1, revision: 1, enabled: true, text: '共通指示テスト: まず docs を読む',
  }), 'utf8');
  return {
    repo,
    config: {
      orchestration: { instructionsDir },
      cowork: { roots: [repo], historyFile },
    },
    historyFile,
  };
}

test('runAdhoc は未登録フォルダと空の指示を拒否する', () => {
  const { config } = makeConfig();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'not-registered-'));
  assert.throws(() => cowork.runAdhoc(config, { root: outside, prompt: 'x' }),
    /登録されていないフォルダ/);
  assert.throws(() => cowork.runAdhoc(config, { root: '', prompt: 'x' }),
    /フォルダを選択/);
  assert.throws(() => cowork.runAdhoc(config, { root: outside, prompt: '  ' }),
    /指示を入力/);
});

test('runAdhoc は登録フォルダで共通指示つきプロンプトを起動し、履歴を type:adhoc で残す', () => {
  const { repo, config, historyFile } = makeConfig();
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = cowork.runAdhoc(config, { root: repo, prompt: '直近の変更をレビューして\n対象は main' });
    assert.strictEqual(r.ok, true, `起動できる: ${r.error || ''}`);
    assert.strictEqual(r.launched, true, '別ウィンドウ起動として返る');
    // 定常業務のセッション（kiro-dash-…）へ合流しない専用の名前空間
    assert.match(r.session || '', /^agent-chat-adhoc-/);
    const body = fs.readFileSync(r.scriptFile, 'utf8');
    assert.ok(body.includes('agent-instructions'), '共通指示のマーカーが入る');
    assert.ok(body.includes('共通指示テスト'), '共通指示の本文が入る');
    assert.ok(body.includes('直近の変更をレビューして'), '自由文が入る');
    assert.ok(body.indexOf('共通指示テスト') < body.indexOf('直近の変更をレビューして'),
      '共通指示は自由文より前に置く');
    const records = fs.readFileSync(historyFile, 'utf8').split(/\r?\n/).filter(Boolean)
      .map((line) => JSON.parse(line));
    assert.strictEqual(records.length, 1);
    assert.strictEqual(records[0].type, 'adhoc');
    assert.strictEqual(records[0].name, '直近の変更をレビューして', '名前はプロンプトの1行目');
    assert.strictEqual(records[0].ok, true);
    assert.ok(records[0].key.startsWith('adhoc|'), '履歴キーは adhoc 種別');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('overview は起動先候補（登録済みフォルダ）を roots として返す', () => {
  const { repo, config } = makeConfig();
  const ov = cowork.overview(config);
  assert.ok(Array.isArray(ov.roots));
  assert.ok(ov.roots.includes(repo), '登録したフォルダが候補に載る');
  // runAdhoc の検証と同じ集合（adhocRoots）を見る
  assert.deepStrictEqual(ov.roots, cowork.adhocRoots(config));
});

console.log(`\n${passed} cowork-adhoc tests passed`);
