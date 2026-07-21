'use strict';

// 指示の「受理」可視化を検証する（commands/*.err の失敗可視化と対称の成功側）。
// 承認などの取り込みは非同期（commands/ ドロップ → 本体が後で処理）で、成功すると本体は
// 元ファイルを消すだけだった。そのため画面からは「保留中（本体が未取り込み）」と「受理済み」を
// 区別できず、押しても何も起きないように見えた（原因不明の停滞）。本体は成功時に
// commands/processed/<name>.json へ受理レシートを残す（_write_command_receipt）。
//   - listCommandReceipts（main）: commands/processed/*.json → タスク id ごとの最新の受理
//   - commandReceiptHtml（renderer）: 受理をカード上に出す（失敗があるときは出さない）

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

test('listCommandReceipts は processed/*.json をタスク id ごと最新 1 件にまとめる', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-cmdrcpt-'));
  const pdir = path.join(dir, 'commands', 'processed');
  fs.mkdirSync(pdir, { recursive: true });
  const write = (name, rec) =>
    fs.writeFileSync(path.join(pdir, name), JSON.stringify(rec), 'utf8');
  write('viewer-approve-T1-1.json', {
    ok: true, action: 'approve', id: 'T1',
    processed_at: '2026-07-21 10:00:00', source: 'viewer-approve-T1-1.json',
  });
  write('viewer-approve-T1-2.json', {
    ok: true, action: 'approve', id: 'T1',
    processed_at: '2026-07-21 10:05:12', source: 'viewer-approve-T1-2.json',
  });
  write('viewer-hold-T2-1.json', {
    ok: true, action: 'hold', id: 'T2', processed_at: '2026-07-21 09:00:00',
  });
  write('proj.json', { ok: true, action: 'replan', id: '' }); // id 無し（プロジェクト単位）は対象外
  write('notok.json', { ok: false, action: 'approve', id: 'T3' }); // ok:false は対象外

  const out = project.listCommandReceipts(dir);
  assert.deepStrictEqual(Object.keys(out).sort(), ['T1', 'T2']);
  assert.strictEqual(out.T1.action, 'approve');
  assert.strictEqual(out.T1.processedAt, '2026-07-21 10:05:12'); // 最新が勝つ
  assert.strictEqual(out.T1.source, 'viewer-approve-T1-2.json');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('listCommandReceipts は processed/ が無ければ空を返す', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-cmdrcpt-'));
  assert.deepStrictEqual(project.listCommandReceipts(dir), {});
  fs.rmSync(dir, { recursive: true, force: true });
});

// eslint-disable-next-line no-new-func
const commandReceiptHtml = new Function(
  `const esc = (s) => String(s);
   const COMMAND_ACTION_LABELS = { approve: '承認', hold: '保留' };
   ${grab('commandReceiptHtml')}; return commandReceiptHtml;`
)();

test('commandReceiptHtml は受理を確認として出す', () => {
  assert.strictEqual(commandReceiptHtml({}), '');
  const html = commandReceiptHtml({
    commandReceipt: { action: 'approve', processedAt: '2026-07-21 10:05:12' },
  });
  assert.match(html, /承認/);
  assert.match(html, /受理されました/);
  assert.match(html, /2026-07-21 10:05:12/);
});

test('commandReceiptHtml は失敗があるときは受理を出さない（失敗表示を上書きしない）', () => {
  const html = commandReceiptHtml({
    commandReceipt: { action: 'approve', processedAt: '2026-07-21 10:05:12' },
    commandFailure: { action: 'approve', error: 'x' },
  });
  assert.strictEqual(html, '');
});

test('renderNeedDetail は受理確認をカードに含める', () => {
  // 実 DOM なしで契約をソースに固定する（commandReceiptHtml の呼び出しが消えると退行）。
  assert.match(renderer, /\$\{commandReceiptHtml\(n\)\}/);
});

console.log(`\n${passed} passed`);
