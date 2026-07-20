'use strict';

// 「承認して完了にする」を押しても要対応が未対応へ戻ってくる問題の可視化を検証する。
// 承認の取り込みは非同期（commands/ ドロップ → 本体が後で処理）なので、送信時トーストは
// 成功しか言えない。本体が取り込みに失敗すると指示は commands/*.err へ退避されるが、
// 以前はそれを誰も読まず、画面は成功トースト＋未対応カードの再表示だけになっていた
// （原因が毎回違っても症状が同じに見え、「同じバグの再発」として繰り返し報告された）。
//   - listCommandFailures（main）: commands/*.err → タスク id ごとの最新の失敗
//   - needBucket（renderer）: 失敗があるカードは「送信済み」に隠さず open へ戻す
//   - commandFailureHtml（renderer）: 失敗理由をカード上に出す

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const project = require('../src/main/project');
const renderer = require('./helpers/renderer-src').read();

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = at;
  while (i < renderer.length && renderer[i] !== '{') i++;
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

test('listCommandFailures は .err をタスク id ごと最新 1 件にまとめる', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-cmderr-'));
  const cdir = path.join(dir, 'commands');
  fs.mkdirSync(cdir);
  const write = (name, rec) =>
    fs.writeFileSync(path.join(cdir, name), JSON.stringify(rec), 'utf8');
  write('a.json.err', {
    error: '古い失敗',
    failed_at: '2026-07-19 10:00:00',
    command: { command: 'approve', id: 'T1' },
  });
  write('b.json.err', {
    error: '統合で競合しました',
    failed_at: '2026-07-20 11:59:35',
    command: { command: 'approve', id: 'T1' },
  });
  write('c.json.err', { error: 'id なし', failed_at: '2026-07-20', command: {} });
  fs.writeFileSync(path.join(cdir, 'garbage.json.err'), '{oops', 'utf8');
  write('pending.json', { command: 'approve', id: 'T2' }); // .err でないものは対象外

  const out = project.listCommandFailures(dir);
  assert.deepStrictEqual(Object.keys(out), ['T1']);
  assert.strictEqual(out.T1.action, 'approve');
  assert.strictEqual(out.T1.error, '統合で競合しました');
  assert.strictEqual(out.T1.failedAt, '2026-07-20 11:59:35');
  fs.rmSync(dir, { recursive: true, force: true });
});

// eslint-disable-next-line no-new-func
const needBucket = new Function(`${grab('needBucket')}; return needBucket;`)();

test('needBucket は取り込み失敗のあるカードを送信済みに隠さない', () => {
  const sent = () => true; // localStorage 上は送信済み扱い
  assert.strictEqual(needBucket({ decided: false }, sent), 'sent');
  assert.strictEqual(
    needBucket({ decided: false, commandFailure: { action: 'approve', error: 'x' } }, sent),
    'open'
  );
  assert.strictEqual(
    needBucket({ decided: true, commandFailure: { action: 'approve', error: 'x' } }, sent),
    'done' // 決着済みはそのまま
  );
});

// eslint-disable-next-line no-new-func
const commandFailureHtml = new Function(
  `const esc = (s) => String(s);
   const COMMAND_ACTION_LABELS = { approve: '承認' };
   ${grab('commandFailureHtml')}; return commandFailureHtml;`
)();

test('commandFailureHtml は失敗理由と操作種別をカードに出す', () => {
  assert.strictEqual(commandFailureHtml({}), '');
  const html = commandFailureHtml({
    commandFailure: {
      action: 'approve',
      error: '成果ブランチをターゲットへ統合できないため done にできません',
      failedAt: '2026-07-20 11:59:35',
    },
  });
  assert.match(html, /承認/);
  assert.match(html, /統合できない/);
  assert.match(html, /2026-07-20 11:59:35/);
  assert.match(html, /role="alert"/);
});

test('renderNeedDetail は取り込み失敗時に操作を出し直す（送信済み扱いにしない）', () => {
  // 実 DOM なしで意図をソース契約として固定する: settled の計算が commandFailure を
  // 考慮していること（これが落ちると「送信済み」のまま操作が消える退行）。
  assert.match(renderer, /n\.decided \|\| \(!n\.commandFailure && isNeedSent\(n\)\)/);
});

console.log(`passed ${passed} tests`);
