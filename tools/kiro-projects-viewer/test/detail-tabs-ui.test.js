'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');

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
const needsViewModel = new Function(
  `${grab('needBucket')}; ${grab('needsViewModel')}; return needsViewModel;`
)();

// eslint-disable-next-line no-new-func
const stabilizeMilestoneNeeds = new Function(
  `${grab('milestoneStatusFor')}; ${grab('stabilizeMilestoneNeeds')}; return stabilizeMilestoneNeeds;`
)();

// eslint-disable-next-line no-new-func
const relatedRunIdForNeed = new Function(`${grab('relatedRunIdForNeed')}; return relatedRunIdForNeed;`)();
// eslint-disable-next-line no-new-func
const formatNeedFullOutput = new Function(`${grab('formatNeedFullOutput')}; return formatNeedFullOutput;`)();
// eslint-disable-next-line no-new-func
const taskForNeed = new Function(`${grab('taskForNeed')}; return taskForNeed;`)();
// eslint-disable-next-line no-new-func
const buildNeedVerifyRevision = new Function(
  `${grab('taskForNeed')}; ${grab('buildNeedVerifyRevision')}; return buildNeedVerifyRevision;`
)();
// eslint-disable-next-line no-new-func
const needVerifyRevisionHtml = new Function(
  `${grab('esc')}; ${grab('taskForNeed')}; ${grab('needVerifyRevisionHtml')}; return needVerifyRevisionHtml;`
)();
// eslint-disable-next-line no-new-func
const verifyRevisionConfirmMessage = new Function(
  `${grab('verifyRevisionConfirmMessage')}; return verifyRevisionConfirmMessage;`
)();

assert.strictEqual(
  taskForNeed(
    { backlog: [{ id: 'T1', title: '検証を直す' }], archive: [{ id: 'T2', title: '完了済み' }] },
    { id: 'N1', taskId: 'T1', kind: 'blocked' }
  ).title,
  '検証を直す',
  '要対応のtaskIdから再実行対象のbacklogタスクを解決する'
);
assert.strictEqual(taskForNeed({ backlog: [] }, { id: 'missing' }), null, '関連タスクが無ければ操作対象にしない');

assert.deepStrictEqual(
  buildNeedVerifyRevision(
    { backlog: [{ id: 'T1', verify: 'npm test' }] },
    { id: 'N1', taskId: 'T1', kind: 'blocked' },
    'npm test -- --runInBand',
    'CI環境では直列実行する'
  ),
  {
    action: 'revise',
    id: 'T1',
    fields: { verify: 'npm test -- --runInBand' },
    feedback: 'CI環境では直列実行する',
    reason: '要対応画面で検証コマンドを変更',
  },
  '検証コマンドの変更はタスク分解ではなく既存reviseへ渡す'
);
assert.strictEqual(
  buildNeedVerifyRevision(
    { backlog: [{ id: 'T1', verify: 'npm test' }] },
    { id: 'T1', kind: 'blocked' },
    ' npm test ',
    ''
  ),
  null,
  '検証コマンドが同一で補足指示も無ければ再実行を送らない'
);
assert.deepStrictEqual(
  buildNeedVerifyRevision(
    { backlog: [{ id: 'T1', verify: 'npm test' }] },
    { id: 'T1', kind: 'blocked' },
    'npm test',
    '失敗ログも確認する'
  ).fields,
  {},
  '補足だけを送る場合は未変更のverifyを置換しない'
);
const verifyConfirm = verifyRevisionConfirmMessage(
  { id: 'T1', verify: 'npm test' },
  { fields: { verify: 'npm run test:ci' } }
);
for (const expected of ['npm test', 'npm run test:ci', 'タスク分解', '履歴']) {
  assert.ok(verifyConfirm.includes(expected), `確認文に「${expected}」が必要`);
}

const verifyRevisionHtml = needVerifyRevisionHtml(
  { backlog: [{ id: 'T1', verify: 'npm test' }] },
  { id: 'N1', taskId: 'T1', kind: 'blocked' }
);
assert.ok(verifyRevisionHtml.includes('検証コマンドを変更'));
assert.ok(verifyRevisionHtml.includes('npm test'));
assert.ok(verifyRevisionHtml.includes('変更して再実行'));
assert.strictEqual(
  needVerifyRevisionHtml({ backlog: [{ id: 'T1', verify: 'npm test' }] }, { id: 'T1', kind: 'review' }),
  '',
  '検証失敗ではない要対応に変更パネルを出さない'
);
assert.strictEqual(
  needVerifyRevisionHtml({ backlog: [] }, { id: 'T1', kind: 'blocked' }),
  '',
  '関連タスクが無い要対応に変更パネルを出さない'
);
assert.ok(
  verifyRevisionConfirmMessage({ id: 'T1', verify: 'npm test' }, { fields: { verify: '' } }).includes('（削除）'),
  '検証コマンドを空にする操作は削除と明示する'
);

// eslint-disable-next-line no-new-func
const renderNeedDetailWithVerifyRevision = new Function(
  'isNeedSent', 'esc', 'needKindLabel', 'riskBadgeHtml', 'needDisplayTitle', 'NEED_ASK',
  'renderNeedFacts', 'needActionsHtml', 'specFilesHtml', 'mdToHtml', 'needVerifyRevisionHtml',
  `${grab('renderNeedDetail')}; return renderNeedDetail;`
)(
  () => false,
  (value) => String(value == null ? '' : value),
  () => '対応依頼',
  () => '',
  (need) => need.title,
  { blocked: '対応方法を指示してください。' },
  () => '',
  () => '<div>回答欄</div>',
  () => '',
  (value) => value,
  needVerifyRevisionHtml
);
assert.ok(
  renderNeedDetailWithVerifyRevision(
    { backlog: [{ id: 'T1', verify: 'npm test' }] },
    { id: 'T1', title: '検証失敗', kind: 'blocked', decided: false }
  ).includes('need-verify-revision'),
  '検証コマンド変更パネルをblocked要対応の詳細に組み込む'
);

const needs = [
  { id: 'open-old', date: '2026-07-12' },
  { id: 'done', date: '2026-07-14', decided: true },
  { id: 'sent', date: '2026-07-13', sent: true },
  { id: 'open-new', date: '2026-07-15' },
];
const sent = (need) => Boolean(need.sent);
const openModel = needsViewModel(needs, 'open', 'missing', sent);
assert.deepStrictEqual(openModel.counts, { open: 2, sent: 1, done: 1 });
assert.deepStrictEqual(openModel.items.map((need) => need.id), ['open-new', 'open-old']);
assert.strictEqual(openModel.selectedId, 'open-new', '選択対象が無い場合は最新の項目を開く');
assert.strictEqual(needsViewModel(needs, 'sent', 'sent', sent).selectedId, 'sent');
assert.strictEqual(needsViewModel(needs, 'gitlab', null, sent).selected, null);

const milestone = { id: 'demo-v1', kind: 'milestone', summary: '判断待ち' };
const review = { id: 'T1', kind: 'review' };
const previousProject = { dir: '/demo', needs: [milestone, review] };
const transientProject = {
  dir: '/demo',
  needs: [review],
  charters: [{ name: 'v1' }],
  projectState: { charters: { v1: { id: 'demo-v1', status: 'converged' } } },
};
assert.deepStrictEqual(
  stabilizeMilestoneNeeds(previousProject, transientProject).map((need) => need.id),
  ['T1', 'demo-v1'],
  '再評価中の一時削除では、判断待ち状態のマイルストーンを維持する'
);
let snapshot = previousProject;
for (let i = 0; i < 3; i++) {
  const incoming = i % 2 === 0 ? transientProject : { ...transientProject, needs: [review, milestone] };
  snapshot = { ...incoming, needs: stabilizeMilestoneNeeds(snapshot, incoming) };
  assert.ok(snapshot.needs.some((need) => need.id === 'demo-v1'), '連続ポーリングでも点滅させない');
}
const acceptedProject = {
  ...transientProject,
  projectState: { charters: { v1: { id: 'demo-v1', status: 'accepted' } } },
};
assert.deepStrictEqual(
  stabilizeMilestoneNeeds(previousProject, acceptedProject).map((need) => need.id),
  ['T1'],
  '承認済みへ変わったマイルストーンは維持しない'
);
assert.deepStrictEqual(
  stabilizeMilestoneNeeds(previousProject, { ...transientProject, dir: '/other' }).map((need) => need.id),
  ['T1'],
  '別プロジェクトのマイルストーンを持ち越さない'
);

assert.strictEqual(
  relatedRunIdForNeed(
    { backlog: [{ id: 'T1', extra: { last_run: 'run-last' } }], archive: [] },
    { id: 'N1', taskId: 'T1' },
    [{ runId: 'run-fallback', taskId: 'T1' }]
  ),
  'run-last',
  '要対応に対応するタスクのlast_runを優先する'
);
const longOutput = `先頭-${'x'.repeat(5000)}-末尾`;
const fullOutput = formatNeedFullOutput(
  { body: 'needs原文' },
  { run: { runId: 'run-last', failureReason: '失敗全体', nodes: { verify: { id: 'verify', goal: '検証', output: longOutput } } }, events: [] }
);
assert.ok(fullOutput.includes('needs原文'));
assert.ok(fullOutput.includes('run-last') && fullOutput.includes('工程 verify'));
assert.ok(fullOutput.includes(longOutput), '5000文字を超える工程出力も省略しない');

// flowGroupBucket は表示分類だけを検証するため、助言判定を小さなスタブで注入する。
// eslint-disable-next-line no-new-func
const flowGroupBucket = new Function(
  'runAdvice',
  'TERMINAL_RUN_STATES',
  `${grab('flowGroupBucket')}; return flowGroupBucket;`
)((latest, group) => group.advice, new Set(['done', 'failed', 'canceled']));
assert.strictEqual(flowGroupBucket({ advice: { kind: 'human' }, latest: { status: 'running' } }), 'action');
assert.strictEqual(flowGroupBucket({ advice: { kind: 'manual' }, latest: { status: 'running' } }), 'action');
assert.strictEqual(flowGroupBucket({ advice: { kind: 'restart' }, latest: { status: 'failed' } }), 'action');
assert.strictEqual(flowGroupBucket({ advice: { kind: 'none' }, latest: { status: 'done' } }), 'done');
assert.strictEqual(flowGroupBucket({ advice: { kind: 'auto' }, latest: { status: 'running' } }), 'active');

for (const label of ['未対応', '送信済み', '回答済み', 'GitLab', '実行中', '要確認', '完了']) {
  assert.ok(renderer.includes(label), `絞り込みに「${label}」が必要です`);
}
for (const label of ['概要', '工程', '履歴']) {
  assert.ok(renderer.includes(`'${label}'`), `実行詳細に「${label}」ビューが必要です`);
}
assert.match(renderer, /class="master-detail/);
assert.match(renderer, /class="flow-detail-shell/);
assert.match(renderer, /data-needs-back/);
assert.match(renderer, /data-flow-back/);
assert.match(css, /\.master-detail/);
assert.match(css, /\.flow-view-tabs/);
assert.match(css, /\.mobile-master-back/);
for (const id of ['btn-git-heal', 'btn-doctor', 'dlg-doctor', 'dlg-need-output', 'dlg-delivery-review']) {
  assert.ok(html.includes(`id="${id}"`), `${id} が画面に必要です`);
}
for (const cli of ['kiro', 'claude', 'copilot', 'codex', 'cursor', 'ollama']) {
  assert.ok(html.includes(`<option value="${cli}"`), `${cli} を設定で選択できる必要があります`);
}
assert.ok(renderer.includes('出力全体を見る'));
assert.ok(renderer.includes('検収物を確認'));
assert.ok(renderer.includes('openDeliveryReview'));
assert.match(renderer, /entry\.path && entry\.base && entry\.ref/);
assert.ok(!/entry\.ref \|\| entry\.branch/.test(renderer), '未解決 ref で差分ボタンを出さない');
assert.match(css, /\.doctor-tools/);
assert.match(css, /\.delivery-dialog/);

console.log('detail-tabs-ui: all tests passed');
