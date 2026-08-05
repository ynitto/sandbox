'use strict';

// 強制完了（force-complete）— どうにも進まないタスクを人の判断で打ち切って完了にする口。
//
// 承認（approve + complete）は検収待ち（review / blocked かつ成果あり）にしか効かない。
// 実行中（doing）・委譲中（offloaded）・実行待ち（ready）で堂々巡りしているタスクは、
// どのボタンを押しても done にできず、承認すると ready へ積み直されて同じ工程が
// また止まる往復になっていた。その袋小路から抜ける最後の口がこれ。
//
// ここで守る約束:
//   - 理由の記入が必須（決定記録に残る人の判断なので、根拠なしには送らせない）
//   - commands/ ドロップ一本（他の指示と同じ経路。本体が取り込んで done 確定する）
//   - 画面は「検証しない・履歴には未検証として残る」ことを押す前に言う（確認ダイアログ）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const renderer = require('./helpers/renderer-src').read();

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-force-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  return dir;
}

function readDropped(dir) {
  const cdir = path.join(dir, 'commands');
  const files = fs.readdirSync(cdir).filter((f) => f.endsWith('.json'));
  assert.strictEqual(files.length, 1, 'commands に 1 件だけドロップされる');
  return { file: files[0], rec: JSON.parse(fs.readFileSync(path.join(cdir, files[0]), 'utf8')) };
}

// renderer の関数を 1 つだけ取り出して評価する（他テストと同じ手口）。引数リストを先に
// 読み飛ばすのは、分割代入の既定値（{ runs = [] } = {}）の `{` を本体と間違えないため。
function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = renderer.indexOf('(', at);
  let depth = 0;
  for (; i < renderer.length; i += 1) {
    if (renderer[i] === '(') depth += 1;
    else if (renderer[i] === ')') {
      depth -= 1;
      if (depth === 0) { i += 1; break; }
    }
  }
  while (i < renderer.length && renderer[i] !== '{') i += 1;
  depth = 0;
  for (; i < renderer.length; i += 1) {
    if (renderer[i] === '{') depth += 1;
    else if (renderer[i] === '}') {
      depth -= 1;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

(async () => {
  await test('force-complete は理由付きの指示ファイルを commands/ へドロップする', async () => {
    const dir = mkProject();
    const res = await actions.runAction({}, {
      dir,
      action: 'force-complete',
      id: 'T1',
      reason: '外部要因で完了不能。ここで打ち切る',
    });
    assert.strictEqual(res.via, 'file');
    const { file, rec } = readDropped(dir);
    assert.ok(file.startsWith('viewer-force-complete-'), `ファイル名: ${file}`);
    assert.strictEqual(rec.command, 'force-complete');
    assert.strictEqual(rec.id, 'T1');                     // タスク単位（プロジェクト単位ではない）
    assert.strictEqual(rec.reason, '外部要因で完了不能。ここで打ち切る');
    assert.strictEqual(rec.actor, 'agent-dashboard');
    assert.ok(rec.ts, '投函時刻が入る');
  });

  await test('理由が空の強制完了は投函せずエラーにする', async () => {
    const dir = mkProject();
    await assert.rejects(
      () => actions.runAction({}, { dir, action: 'force-complete', id: 'T1', reason: '   ' }),
      /理由の記入が必要/
    );
    // 他の指示（既定理由で通る）と違い、ファイルそのものを作らない
    assert.ok(!fs.existsSync(path.join(dir, 'commands')), '指示ファイルは作られない');
  });

  await test('理由の必須化は強制完了だけ（承認などは従来どおり既定理由で通る）', async () => {
    const dir = mkProject();
    const res = await actions.runAction({}, { dir, action: 'approve', id: 'T1', reason: '' });
    assert.strictEqual(res.via, 'file');
    assert.strictEqual(readDropped(dir).rec.reason, 'agent-dashboard から操作');
  });

  await test('タスク詳細の操作タブに強制完了ボタンがあり、確認を挟む', () => {
    assert.match(renderer, /data-taskact="force-complete"[^>]*data-confirm-force="1"/);
    assert.match(renderer, /強制的に完了にする/);
    // 理由なしでは送らない・確認ダイアログを通す
    assert.match(renderer, /btn\.dataset\.confirmForce/);
    assert.match(renderer, /forceCompleteConfirmMessage\(p, t\)/);
  });

  await test('要対応カード（作業再開）から打ち切って完了にできる', () => {
    assert.match(renderer, /data-act="force-complete"[^>]*data-require-force="1"/);
    assert.match(renderer, /打ち切って完了/);
    assert.match(renderer, /btn\.dataset\.requireForce/);
    assert.match(renderer, /action: 'force-complete', id, reason: text/);
  });

  await test('確認文は「検証しない・未検証として残る」ことを先に言う', () => {
    // eslint-disable-next-line no-new-func
    const build = new Function(
      'statusLabel', 'dependentsOf',
      `${grab('forceCompleteConfirmMessage')}; return forceCompleteConfirmMessage;`
    )((s) => ({ doing: '実行中' }[s] || s), () => []);
    const msg = build({ backlog: [] }, { id: 'T1', status: 'doing' });
    assert.match(msg, /T1 を強制的に完了にします（現在: 実行中）/);
    assert.match(msg, /検証（verify）は実行しません/);
    assert.match(msg, /成果ブランチの自動統合も行いません/);
    assert.match(msg, /FORCED（未検証）/);
    // 状態を持たない合成票からの呼び出しでも壊れない
    assert.match(build({}, { id: 'T9', status: '' }), /T9 を強制的に完了にします。/);
  });

  await test('実行中タスクの「完了まで」に打ち切りの逃げ道が案内される', () => {
    // eslint-disable-next-line no-new-func
    const hint = new Function(
      `${grab('taskCompletionHint')}; return taskCompletionHint;`
    )();
    const h = hint({ status: 'doing', extra: {} }, { runs: [] });
    assert.match(h.completeHow, /強制的に完了にする/);
  });

  console.log(`\n${passed} tests passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
