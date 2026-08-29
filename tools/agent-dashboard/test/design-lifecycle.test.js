'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const adhocPreload = require('../src/features/adhoc-flow/preload');
const design = require('../src/features/adhoc-flow/main/design-session');
const workflowUi = require('../src/renderer/features/adhoc-flow');

test('preload は設計の差し戻し理由を専用 IPC channel へ渡す', () => {
  const calls = [];
  const invoke = (...args) => { calls.push(args); return { ok: true }; };

  adhocPreload.preparationReviseDesign(invoke)({ id: 'prep-1', feedback: '検証方法を具体化してください' });

  assert.deepStrictEqual(calls, [[
    'preparation:reviseDesign', { id: 'prep-1', feedback: '検証方法を具体化してください' },
  ]]);
});

test('設計フローの表示にはユーザー承認・完了・差し戻しのDashboard工程を合成する', () => {
  const workflow = adhoc.loadWorkflow({ adhocFlow: {} }, 'design-auto', {
    scope: 'builtin', purpose: 'design',
  });
  const visual = workflowUi.visualWorkflow(workflow);
  const byId = new Map(visual.nodes.map((node) => [node.id, node]));

  assert.deepStrictEqual(byId.get('__design-user-review__').deps, ['review']);
  assert.deepStrictEqual(byId.get('__design-complete__').deps, ['__design-user-review__']);
  assert.deepStrictEqual(byId.get('__design-revise__').deps, ['__design-user-review__']);
  assert.deepStrictEqual(byId.get('__design-revise__').runtimeReturns, ['requirements']);
  assert.deepStrictEqual(visual.exit, ['__design-complete__']);
  assert.ok(['__design-user-review__', '__design-complete__', '__design-revise__']
    .every((id) => byId.get(id).runtime && byId.get(id).lifecycle));
});

test('差し戻し理由は現在の設計書と一緒に次ラウンドへ渡す', () => {
  const request = design.buildRoundRequest({
    goal: 'CSV対応を改善する',
    document: '## 目的\n既存の設計',
    feedback: '受入基準を観測可能な表現に直してください',
  });

  assert.ok(request.includes('## 現在の設計書\n## 目的\n既存の設計'));
  assert.ok(request.includes('## ユーザーからの差し戻し\n受入基準を観測可能な表現に直してください'));
});

test('設計 run の進捗を画面向けの工程数と状態へ正規化する', () => {
  assert.deepStrictEqual(design.runProgress({
    phase: 'executing', alive: true, heartbeatAt: '2026-08-26T20:12:01Z',
    total: 4, progress: 0.5,
    counts: { done: 2, failed: 0, claimed: 1, parked: 0, pending: 1, waiting: 0 },
  }), {
    phase: 'executing', alive: true, heartbeatAt: '2026-08-26T20:12:01Z',
    total: 4, completed: 2, active: 1, pending: 1, percent: 50,
  });
});

test('設計 run の工程別件数が部分的でも総数から待機数を補完する', () => {
  assert.deepStrictEqual(design.runProgress({
    phase: 'executing', total: 5,
    counts: { done: 1, claimed: 1 },
  }), {
    phase: 'executing', alive: null, heartbeatAt: null,
    total: 5, completed: 1, active: 1, pending: 3, percent: 20,
  });
});

test('設計中は設計確認を無効化し、設計完了後だけ確認操作を有効にする', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const running = workflowUi.preparationActionsHtml({ id: 'prep-1', phase: 'designing' });
    assert.match(running, /disabled/);
    assert.match(running, /設計を確認/);
    assert.doesNotMatch(running, /data-preparation-open/);

    const review = workflowUi.preparationActionsHtml({ id: 'prep-1', phase: 'design-review' });
    assert.match(review, /data-preparation-open="prep-1"/);
    assert.doesNotMatch(review, /disabled/);
  } finally {
    global.esc = previousEsc;
  }
});

test('実装中と完了は作業準備一覧に出さず、左の実行一覧で追う', () => {
  const previousEsc = global.esc;
  const previous = {
    selectedRun: workflowUi._state.selectedRun,
    runDetail: workflowUi._state.runDetail,
    preparationItems: workflowUi._state.preparationItems,
  };
  global.esc = (value) => String(value);
  try {
    workflowUi._state.selectedRun = '';
    workflowUi._state.runDetail = null;
    workflowUi._state.preparationItems = [
      { id: 'prep-wait', title: '実装待ちの仕事', phase: 'implementation-ready', route: 'direct', cwd: '/repo' },
      { id: 'prep-run', title: '実装中の仕事', phase: 'implementing', route: 'direct', cwd: '/repo' },
      { id: 'prep-done', title: '完了した仕事', phase: 'completed', route: 'direct', cwd: '/repo' },
    ];
    const html = workflowUi.runHtml({});
    assert.match(html, /実装待ちの仕事/);
    assert.doesNotMatch(html, /実装中の仕事/);
    assert.doesNotMatch(html, /完了した仕事/);
    assert.match(html, /1件/);
    assert.match(html, /id="wf-active-title">実行中/);
    assert.match(html, /実行中のワークフローはありません/);
  } finally {
    workflowUi._state.selectedRun = previous.selectedRun;
    workflowUi._state.runDetail = previous.runDetail;
    workflowUi._state.preparationItems = previous.preparationItems;
    global.esc = previousEsc;
  }
});

test('トップ画面の実行中枠は終端していない run だけを作業準備と同じカードで出す', () => {
  const previousEsc = global.esc;
  const previous = {
    selectedRun: workflowUi._state.selectedRun,
    runDetail: workflowUi._state.runDetail,
    preparationItems: workflowUi._state.preparationItems,
  };
  global.esc = (value) => String(value);
  try {
    workflowUi._state.selectedRun = '';
    workflowUi._state.runDetail = null;
    workflowUi._state.preparationItems = [];
    const html = workflowUi.runHtml({
      runs: [
        {
          runId: 'run-live', status: 'running', request: 'README を直す',
          workspace: { cwd: '/repo' }, total: 4, counts: { done: 1, failed: 0 },
        },
        {
          runId: 'run-plan', status: 'planning', request: '設計を進める',
        },
        {
          runId: 'run-done', status: 'done', request: '終わった仕事',
        },
        {
          runId: 'run-failed', status: 'failed', request: '失敗した仕事',
        },
      ],
      runInbox: [
        { id: 'run-live', title: '実装中の仕事', purpose: 'implementation' },
        { id: 'run-plan', title: '', purpose: 'design' },
      ],
    });
    const activeAt = html.indexOf('id="wf-active-title">実行中');
    const queueAt = html.indexOf('id="wf-queue-title">作業準備');
    assert.ok(queueAt >= 0 && activeAt > queueAt, '実行中は作業準備の下に置く');
    const active = html.slice(activeAt);
    assert.match(active, /実装中の仕事/);
    assert.match(active, /設計を進める/);
    assert.match(active, /data-open-run="run-live"/);
    assert.match(active, /1\/4 工程/);
    assert.match(active, /設計 · 対象フォルダ未指定/);
    assert.doesNotMatch(active, /終わった仕事/);
    assert.doesNotMatch(active, /失敗した仕事/);
    assert.match(html, />2件</);
  } finally {
    workflowUi._state.selectedRun = previous.selectedRun;
    workflowUi._state.runDetail = previous.runDetail;
    workflowUi._state.preparationItems = previous.preparationItems;
    global.esc = previousEsc;
  }
});

test('ワークフロー領域の着地点は実行待ちトップである', () => {
  workflowUi._state.selectedRun = 'run-1';
  workflowUi._state.runDetail = { runId: 'run-1' };
  workflowUi._state.selectedPreparation = 'prep-1';
  assert.strictEqual(workflowUi.showTop(), 'workflow-run');
  assert.strictEqual(workflowUi._state.selectedRun, '');
  assert.equal(workflowUi._state.runDetail, null);
  assert.strictEqual(workflowUi._state.selectedPreparation, '');
});

test('設計ペインはセッションと現在内容を分け、実行中の工程進捗を表示する', () => {
  const previousEsc = global.esc;
  const previousProseHtml = global.proseHtml;
  global.esc = (value) => String(value);
  global.proseHtml = (value) => `<div class="md">${String(value)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')}</div>`;
  try {
    workflowUi._state.design.sessions = [{ id: 'ds-1', goal: '設計する', runStatus: 'running' }];
    workflowUi._state.design.current = {
      id: 'ds-1', goal: '設計する', runStatus: 'running', rounds: [{ runId: 'run-1' }],
      runProgress: { phase: 'executing', total: 4, completed: 2, active: 1, pending: 1, percent: 50 },
    };
    const html = workflowUi.designCardHtml();
    assert.match(html, /wf-design-workspace/);
    assert.match(html, /wf-design-sidebar/);
    assert.match(html, /wf-design-main/);
    assert.match(html, /role="progressbar"/);
    assert.match(html, /aria-valuenow="50"/);
    assert.match(html, /2\/4 工程/);
  } finally {
    global.esc = previousEsc;
    global.proseHtml = previousProseHtml;
  }
});

test('対話型設計の質問と設計書は Markdown として描画する', () => {
  const previousEsc = global.esc;
  const previousProseHtml = global.proseHtml;
  global.esc = (value) => String(value);
  global.proseHtml = (value) => `<div class="md">${String(value)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')}</div>`;
  try {
    const html = workflowUi.designSessionHtml({
      id: 'ds-2', goal: '設計する', runStatus: 'done', rounds: [{ runId: 'run-2' }],
      document: '## 目的\n**読みやすくする**',
      questions: ['**推奨:** 一覧と詳細のどちらを優先しますか？'],
    });
    assert.match(html, /wf-design-question-copy/);
    assert.match(html, /<strong>推奨:<\/strong>/);
    assert.match(html, /wf-design-document/);
    assert.doesNotMatch(html, /<pre class="qf-output">/);
  } finally {
    global.esc = previousEsc;
    global.proseHtml = previousProseHtml;
  }
});

test('作業準備ラベルは内容幅、設計ペインは2カラムで配置する', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');
  assert.match(css, /\.wf-preparation-phase\s*\{[^}]*justify-self:\s*start/s);
  assert.match(css, /\.wf-design-workspace\s*\{[^}]*grid-template-columns:/s);
});
