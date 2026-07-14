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

assert.match(renderer, /実行済み・未確定/);
assert.match(renderer, /承認（実行を許可・完了にはならない）/);
assert.match(renderer, /need-complete-how/);

console.log('task-completion-hint.test.js: ok');
