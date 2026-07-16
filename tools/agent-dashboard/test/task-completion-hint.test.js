'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let i = at + `function ${name}`.length;
  while (i < renderer.length && renderer[i] !== '(') i++;
  let depth = 0;
  for (; i < renderer.length; i++) {
    const ch = renderer[i];
    if (ch === '(') depth++;
    else if (ch === ')') {
      depth--;
      if (depth === 0) {
        i++;
        break;
      }
    }
  }
  while (i < renderer.length && renderer[i] !== '{') i++;
  const bodyAt = i;
  depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません (body@${bodyAt})`);
}

const STATUS_LABELS = {
  ready: '実行待ち',
  review: '検収待ち',
  blocked: '要対応',
  done: '完了',
  failed: '失敗',
  canceled: '中止',
  running: '実行中',
};
// eslint-disable-next-line no-new-func
const statusLabel = new Function('STATUS_LABELS', `${grab('statusLabel')}; return statusLabel;`)(
  STATUS_LABELS
);
// eslint-disable-next-line no-new-func
const taskCompletionHint = new Function(
  'statusLabel',
  `${grab('taskCompletionHint')}; return taskCompletionHint;`
)(statusLabel);
// eslint-disable-next-line no-new-func
const runStatusCaption = new Function(
  'statusLabel',
  `${grab('runStatusCaption')}; return runStatusCaption;`
)(statusLabel);
// eslint-disable-next-line no-new-func
const runTaskOutcome = new Function(
  'sanitizeTaskId', 'statusLabel',
  `${grab('runTaskOutcome')}; return runTaskOutcome;`
)(
  (id) => String(id == null ? '' : id).replace(/[^\w.-]+/g, '_').slice(0, 60),
  statusLabel
);
// eslint-disable-next-line no-new-func
const runTaskOutcomeHtml = new Function(
  'esc', `${grab('runTaskOutcomeHtml')}; return runTaskOutcomeHtml;`
)((value) => String(value == null ? '' : value));
// eslint-disable-next-line no-new-func
const runTaskOutcomeCompactHtml = new Function(
  'esc', `${grab('runTaskOutcomeCompactHtml')}; return runTaskOutcomeCompactHtml;`
)((value) => String(value == null ? '' : value));
// eslint-disable-next-line no-new-func
const runFinalVerificationFailure = new Function(
  'sanitizeTaskId', `${grab('runFinalVerificationFailure')}; return runFinalVerificationFailure;`
)((id) => String(id == null ? '' : id).replace(/[^\w.-]+/g, '_').slice(0, 60));
// eslint-disable-next-line no-new-func
const finalVerificationFailureHtml = new Function(
  'esc', `${grab('finalVerificationFailureHtml')}; return finalVerificationFailureHtml;`
)((value) => String(value == null ? '' : value));

const doneRun = { runId: 'req-x-T1-r1', status: 'done' };

{
  const h = taskCompletionHint(
    { status: 'ready', extra: { last_run: 'req-x-T1-r1' } },
    { runs: [doneRun] }
  );
  assert.strictEqual(h.unsettledDone, true);
  assert.strictEqual(h.statusNote, '実行済み・未確定');
  assert.match(h.completeHow, /再実行/);
}

{
  const h = taskCompletionHint({ status: 'review', extra: {} }, { runs: [doneRun] });
  assert.strictEqual(h.unsettledDone, false);
  assert.match(h.completeHow, /承認して完了/);
  assert.match(h.needAsk, /納品/);
}

{
  const h = taskCompletionHint(
    {
      status: 'blocked',
      extra: { last_run: 'req-x-T1-r1', env_resume: '1', needs_reason: '[agent-error:env] x' },
    },
    { runs: [doneRun] }
  );
  assert.strictEqual(h.unsettledDone, true);
  assert.strictEqual(h.statusNote, '実行済み・未確定');
  assert.match(h.completeHow, /要対応/);
  assert.match(h.needAsk, /実行自体は終わって/);
}

{
  const h = taskCompletionHint({ status: 'proposed', extra: {} }, { runs: [] });
  assert.match(h.completeHow, /計画を承認/);
  assert.match(h.needAsk, /完了になりません/);
}

{
  const h = taskCompletionHint({ status: 'ready', extra: {} }, { runs: [] });
  assert.strictEqual(h.unsettledDone, false);
  assert.match(h.completeHow, /操作不要/);
}

{
  const h = taskCompletionHint({ status: 'ready', extra: {} }, { runs: [doneRun], archived: true });
  assert.match(h.completeHow, /完了済み/);
}

assert.strictEqual(runStatusCaption('done', { taskArchived: false }), '実行完了（タスク未確定）');
assert.strictEqual(runStatusCaption('done', { taskArchived: true }), '納品済み');
assert.strictEqual(runStatusCaption('failed', { taskArchived: false }), '失敗');

assert.deepStrictEqual(
  runTaskOutcome(
    { backlog: [{ id: 'T1', status: 'review' }], archive: [] },
    { taskId: 'T1', status: 'done' }
  ),
  {
    runLabel: '実行完了',
    runStatus: 'done',
    taskLabel: '検収待ち',
    taskStatus: 'review',
    taskArchived: false,
    taskId: 'T1',
    note: '実行は完了しましたが、タスクはまだ完了していません。',
  }
);

const pendingTaskOutcomeHtml = runTaskOutcomeHtml({
  runLabel: '実行完了',
  runStatus: 'done',
  taskLabel: '検収待ち',
  taskStatus: 'review',
  taskArchived: false,
  taskId: 'T1',
  note: '実行は完了しましたが、タスクはまだ完了していません。',
});
assert.match(pendingTaskOutcomeHtml, />実行</);
assert.match(pendingTaskOutcomeHtml, />実行完了</);
assert.match(pendingTaskOutcomeHtml, />タスク</);
assert.match(pendingTaskOutcomeHtml, />検収待ち</);
assert.match(pendingTaskOutcomeHtml, /タスクはまだ完了していません/);
assert.match(
  runTaskOutcomeHtml({
    runLabel: '失敗', runStatus: 'failed', taskLabel: '要対応', taskStatus: 'blocked',
    taskArchived: false, taskId: 'T1', note: '',
  }),
  /status-chip st-failed[^>]*>失敗</,
  'runの状態色をタスクや完了色と混同しない'
);
const compactOutcome = runTaskOutcomeCompactHtml({
  runLabel: '実行完了', runStatus: 'done', taskLabel: '検収待ち', taskStatus: 'review',
  taskArchived: false, taskId: 'T1', note: '実行は完了しましたが、タスクはまだ完了していません。',
});
assert.match(compactOutcome, />実行完了</);
assert.match(compactOutcome, />タスク: 検収待ち</);

const finalVerifyFailure = runFinalVerificationFailure(
  {
    backlog: [{ id: 'T1', status: 'blocked', extra: { last_run: 'run-final-verify' } }],
    archive: [],
    needs: [{ taskId: 'T1', kind: 'blocked', failureSummary: 'テストが 2 件失敗しました。' }],
  },
  {
    runId: 'run-final-verify', taskId: 'T1', status: 'done', total: 3,
    counts: { done: 3, failed: 0 },
  }
);
assert.deepStrictEqual(finalVerifyFailure, {
  title: '工程は全て成功・最終検証で失敗',
  summary: 'テストが 2 件失敗しました。',
  taskId: 'T1',
});
assert.match(finalVerificationFailureHtml(finalVerifyFailure), /工程は全て成功/);
assert.match(finalVerificationFailureHtml(finalVerifyFailure), /最終検証で失敗/);
assert.match(finalVerificationFailureHtml(finalVerifyFailure), /タスクは未完了/);
assert.strictEqual(
  runFinalVerificationFailure(
    { backlog: [{ id: 'T1', status: 'blocked', extra: { last_run: 'new-run' } }], needs: [] },
    { runId: 'old-run', taskId: 'T1', status: 'done', total: 1, counts: { done: 1, failed: 0 } }
  ),
  null,
  '古いrunを現在の最終検証失敗として表示しない'
);
assert.strictEqual(
  runFinalVerificationFailure(
    { backlog: [{ id: 'T1', status: 'review', extra: { last_run: 'run-ok' } }], needs: [] },
    { runId: 'run-ok', taskId: 'T1', status: 'done', total: 1, counts: { done: 1, failed: 0 } }
  ),
  null,
  '検収待ちは検証失敗と誤認しない'
);

assert.match(renderer, /実行済み・未確定/);
assert.match(renderer, /承認して実行/);
assert.match(renderer, /need-complete-how/);
assert.ok(
  !grab('renderBacklog').includes('unsettleBadge'),
  'タスク一覧の状態列には実行済み・未確定バッジを追加しない'
);

console.log('task-completion-hint.test.js: ok');
