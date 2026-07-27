'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const rendererRoot = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(rendererRoot, 'index.html'), 'utf8');
const orchestration = fs.readFileSync(path.join(rendererRoot, 'sections', 'orchestration.js'), 'utf8');
const overview = fs.readFileSync(path.join(rendererRoot, 'sections', 'overview.js'), 'utf8');
const flow = fs.readFileSync(path.join(rendererRoot, 'sections', 'flow.js'), 'utf8');

assert.ok(html.includes('<h1>プロジェクト管理</h1>'));
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

console.log('ui-copy tests passed');
