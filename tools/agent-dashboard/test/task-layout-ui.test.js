'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
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

// eslint-disable-next-line no-new-func
const taskListItemViewModel = new Function(
  'statusLabel',
  `${grab('taskListItemViewModel')}; return taskListItemViewModel;`
)((status) => ({ ready: '実行待ち' }[status] || status));
// eslint-disable-next-line no-new-func
const taskListItemHtml = new Function(
  'esc', 'statusChip',
  `${grab('taskListItemHtml')}; return taskListItemHtml;`
)(
  (value) => String(value == null ? '' : value),
  (status) => `<span class="status-chip st-${status}">${status}</span>`
);

{
  const model = taskListItemViewModel(
    { id: 'T1', title: '検索結果を安定表示する', status: 'ready', priority: 8 },
    { completeHow: '自動で実行されます', statusNote: '' }
  );
  assert.deepStrictEqual(model, {
    id: 'T1',
    title: '検索結果を安定表示する',
    status: 'ready',
    statusText: '実行待ち',
    priority: 8,
    priorityText: '高 8',
    nextAction: '自動で実行されます',
  });
}

{
  const html = taskListItemHtml({
    id: 'T1',
    title: '検索結果を安定表示する',
    status: 'ready',
    statusText: '実行待ち',
    priority: 8,
    priorityText: '高 8',
    nextAction: '自動で実行されます',
  }, 'backlog');
  for (const className of ['task-list-status', 'task-list-title', 'task-list-priority', 'task-list-next']) {
    assert.ok(html.includes(className), `${className} を固定表示します`);
  }
  assert.match(html, /type="button"[^>]*data-task="T1"[^>]*data-scope="backlog"/);
  assert.match(html, /role="listitem"/);
  assert.ok(!html.includes('再試行'));
  assert.ok(!html.includes('検証'));
  assert.ok(!html.includes('属性'));
}

{
  const source = grab('renderBacklog');
  assert.ok(source.includes('task-list-grid'), 'タスク画面は固定要約一覧を描画します');
  assert.ok(source.includes('taskListItemViewModel('), '一覧用表示モデルを経由します');
  assert.ok(source.includes('taskListItemHtml('), '要約項目の共通構造を使います');
  assert.ok(!source.includes('<table class="list"'), '横長の可変テーブルを使いません');
  assert.ok(source.includes('aria-pressed="${state.backlogFilter === key}"'), '状態フィルターの選択を通知します');
  assert.ok(source.includes('aria-pressed="${((state.backlogCharter || \'\') === v)}"'), 'バージョンフィルターの選択を通知します');
}

{
  assert.match(css, /\.task-list-item\s*\{[^}]*grid-template-columns:\s*112px\s+minmax\(180px,\s*1\.25fr\)\s+88px\s+minmax\(220px,\s*1fr\)\s+24px/s);
  assert.match(css, /\.task-list-title\s*\{[^}]*-webkit-line-clamp:\s*2/s);
  assert.match(css, /@media \(max-width:\s*768px\)[\s\S]*\.task-list-item\s*\{[^}]*grid-template-columns:\s*1fr\s+auto/s);
  assert.match(css, /@media \(max-width:\s*768px\)[\s\S]*\.task-list-header\s*\{[^}]*display:\s*none/s);
}

console.log('task-layout-ui: all tests passed');
