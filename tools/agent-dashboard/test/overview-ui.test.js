'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
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
const overviewSummary = new Function(`${grab('overviewSummary')}; return overviewSummary;`)();

const project = {
  liveness: { running: true, paused: false },
  needs: [{ id: 'N1', decided: false }],
  byStatus: { doing: 2, offloaded: 1, ready: 3, inbox: 1, proposed: 1 },
  claims: ['T1'],
  archive: [{ id: 'D1' }, { id: 'D2' }],
  backlog: [
    { id: 'T1', status: 'doing' },
    { id: 'T2', status: 'offloaded' },
    { id: 'T3', status: 'ready' },
    { id: 'T4', status: 'inbox' },
    { id: 'T5', status: 'proposed' },
  ],
};

const summary = overviewSummary(project, [
  { status: 'running' },
  { status: 'done' },
  { status: 'failed' },
]);
assert.strictEqual(summary.headline, '1 件の確認を待っています');
assert.strictEqual(summary.working, 3);
assert.strictEqual(summary.waiting, 5);
assert.strictEqual(summary.done, 2);
assert.strictEqual(summary.total, 7);
assert.strictEqual(summary.progress, 29);
assert.strictEqual(summary.activeRuns, 1);

assert.ok(!html.includes('id="btn-mode"'), '表示モード切替を残さない');
assert.match(html, /data-tab="overview">概要/);
assert.match(html, /data-tab="backlog">タスク/);
assert.match(html, /data-tab="flow">実行/);
assert.match(html, /id="btn-project-settings"/);
assert.ok(!html.includes('id="btn-git-pull"'), '最新取得の単独ボタンを残さない');
assert.ok(!html.includes('id="btn-git-heal"'), '同期修復を Doctor の固定ボタンとして残さない');
assert.match(html, /表示を更新（このPCのファイルを読み直す）/);
assert.match(html, /id="project-meta"[^>]+aria-live="polite"/);
assert.match(renderer, /id="btn-sync-now"/);
assert.match(renderer, /共有先と同期/);
assert.match(renderer, /同期を修復/);
assert.match(renderer, /共有先確認:/);
assert.match(renderer, /remoteCheckedAt/);
assert.match(renderer, /refreshAll\(\{ sync: false \}\)/);
assert.match(renderer, /reloadProject\(\{ refreshRemoteHealth: sync \}\)/);
assert.match(renderer, /api\.gitHealth\(project\.dir, refreshRemoteHealth\)/);

for (const label of ['現在の状態', 'あなたの対応', '進捗', '成果', '対応する', 'タスクを見る', '実行を見る', '成果を見る']) {
  assert.ok(renderer.includes(label), `概要に「${label}」が必要です`);
}
assert.match(css, /button:focus-visible/);
assert.match(css, /@media \(max-width: 680px\)/);

console.log('overview-ui: all tests passed');
