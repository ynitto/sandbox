'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const rendererRoot = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(rendererRoot, 'index.html'), 'utf8');
const orchestration = fs.readFileSync(path.join(rendererRoot, 'sections', 'orchestration.js'), 'utf8');
const overview = fs.readFileSync(path.join(rendererRoot, 'sections', 'overview.js'), 'utf8');
const flow = fs.readFileSync(path.join(rendererRoot, 'sections', 'flow.js'), 'utf8');
const nodeDetail = fs.readFileSync(path.join(rendererRoot, 'sections', 'node-detail.js'), 'utf8');
const backlog = fs.readFileSync(path.join(rendererRoot, 'sections', 'backlog.js'), 'utf8');

function maxDetailsDepth(source) {
  let depth = 0;
  let max = 0;
  for (const match of source.matchAll(/<\/?details\b[^>]*>/g)) {
    if (match[0].startsWith('</')) depth -= 1;
    else {
      depth += 1;
      max = Math.max(max, depth);
    }
  }
  return max;
}

// アプリの見出しはポータル（agent-* ファミリー全体の横断ビュー）としての名前。
// 「プロジェクト管理」は agent-project 専用面だった頃の名前で、戻さない。
assert.ok(html.includes('<h1>ポータル</h1>'));
assert.ok(html.includes('<title>ポータル</title>'));
assert.ok(!html.includes('<h1>プロジェクト管理</h1>'));
assert.ok(html.includes('この作業を相談'));
assert.ok(!html.includes('AIに相談'));
assert.ok(!html.includes('担当の構成をJSONで指定します'));
assert.ok(!html.includes('kiro-loop.yml に反映'));
assert.ok(!html.includes('ステートマシンを作成'));
assert.ok(orchestration.includes('先に全体設定で作業を再開してください。'));
assert.ok(!orchestration.includes('管理面で止まっています'));
assert.ok(overview.includes('この操作は元に戻せません。続けますか？'));
assert.ok(!overview.includes('削除対象: 計画バージョン'));

// 設計メモや実装上の都合を、補足の括弧書きとして UI へ持ち込まない。
for (const leaked of ['（S3）', '（既定非表示）', '（憲章には直接載りません）',
  '（送信は人が確定します）', '（完了済みは温存）', '（タスクを積み直して本体に実行させます）']) {
  assert.ok(!html.includes(leaked) && !flow.includes(leaked), `UI に内部説明を出しません: ${leaked}`);
}
assert.ok(!html.includes('AI 補完'));
assert.ok(!flow.includes('エージェント CLI の実行環境'));
assert.ok(flow.includes('承認を待っています。要対応タブで確認してください。'));

// 工程途中のチェックと、成果全体の受け入れを同じ表示名にしない。
assert.ok(nodeDetail.includes("verify: '工程内チェック'"));
assert.ok(nodeDetail.includes('作業グラフの途中で、後続工程へ進めるかを判断します'));
assert.ok(nodeDetail.includes('完了ゲートで成果全体を確認します'));
assert.ok(nodeDetail.includes('aria-label="完了ゲート"'));
assert.ok(backlog.includes('<summary>固定した検証方法</summary>'));
const enqueueDialog = html.slice(html.indexOf('<dialog id="dlg-enqueue">'), html.indexOf('</dialog>', html.indexOf('<dialog id="dlg-enqueue">')));
const reviseMarkup = backlog.slice(backlog.indexOf('function reviseAreaHtml('), backlog.indexOf('function showTaskDialog('));
assert.strictEqual(maxDetailsDepth(enqueueDialog), 1, 'タスク追加の折りたたみをネストしません');
assert.strictEqual(maxDetailsDepth(reviseMarkup), 1, 'タスク編集の折りたたみをネストしません');
for (const label of ['計画レビュー情報（必須）', '実行順と変更先', '確認方法を固定', 'ほかのタスクを確認']) {
  assert.ok(enqueueDialog.includes(`<summary>${label}</summary>`));
}
for (const label of ['内容と完了条件', '実行順と担当', '確認方法を固定', '目的と変更範囲']) {
  assert.ok(reviseMarkup.includes(`<summary>${label}</summary>`));
}
assert.ok(backlog.includes('role="tablist" aria-label="タスク詳細の表示内容"'));
for (const tab of ['概要', '編集', '操作']) assert.ok(backlog.includes(`>${tab}</button>`));
for (const id of ['rv-feedback', 'rv-title', 'rv-acceptance', 'rv-priority', 'rv-level', 'rv-after',
  'rv-track', 'rv-node', 'rv-note', 'rv-verify']) {
  assert.ok(reviseMarkup.includes(`for="${id}"`), `${id} に関連付けたラベルが必要です`);
}
assert.ok(backlog.includes('<summary>詳細情報</summary>'));
assert.ok(backlog.includes('function requestTaskDialogClose('));
assert.ok(backlog.includes('taskDialogInputSnapshot('));
assert.ok(html.includes('<label for="enq-accept">受入基準</label>'));
assert.ok(html.includes('<label>達成条件</label>'));
assert.ok(!html.includes('accept:'));

console.log('ui-copy tests passed');
