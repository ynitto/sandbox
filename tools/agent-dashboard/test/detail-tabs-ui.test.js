'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
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
// eslint-disable-next-line no-new-func
const deliveryReviewState = new Function(`${grab('deliveryReviewState')}; return deliveryReviewState;`)();
// eslint-disable-next-line no-new-func
const needFailureViewModel = new Function(`${grab('needFailureViewModel')}; return needFailureViewModel;`)();
// eslint-disable-next-line no-new-func
const canDiagnoseNeed = new Function(
  'needFailureViewModel', `${grab('canDiagnoseNeed')}; return canDiagnoseNeed;`
)(needFailureViewModel);
// eslint-disable-next-line no-new-func
const needListSummary = new Function(
  'needFailureViewModel', 'NEED_ASK', `${grab('needListSummary')}; return needListSummary;`
)(needFailureViewModel, { blocked: '対応方法を指示してください。' });
// eslint-disable-next-line no-new-func
const captureNeedsScroll = new Function(`${grab('captureNeedsScroll')}; return captureNeedsScroll;`)();
// eslint-disable-next-line no-new-func
const restoreNeedsScroll = new Function(`${grab('restoreNeedsScroll')}; return restoreNeedsScroll;`)();
// eslint-disable-next-line no-new-func
const completedTaskForNeed = new Function(`${grab('completedTaskForNeed')}; return completedTaskForNeed;`)();
// eslint-disable-next-line no-new-func
const completedRunForNeed = new Function(
  'relatedRunIdForNeed', `${grab('completedRunForNeed')}; return completedRunForNeed;`
)(relatedRunIdForNeed);
// eslint-disable-next-line no-new-func
const needFinalVerificationFailure = new Function(
  'taskForNeed', 'completedRunForNeed', 'needFailureViewModel',
  `${grab('needFinalVerificationFailure')}; return needFinalVerificationFailure;`
)(taskForNeed, completedRunForNeed, needFailureViewModel);
// eslint-disable-next-line no-new-func
const needApprovalReason = new Function(
  `${grab('needApprovalReason')}; return needApprovalReason;`
)();
// eslint-disable-next-line no-new-func
const needAssistActionsHtml = new Function(
  'esc', 'canDiagnoseNeed', `${grab('needAssistActionsHtml')}; return needAssistActionsHtml;`
)((value) => String(value == null ? '' : value), canDiagnoseNeed);
// eslint-disable-next-line no-new-func
const newestDoneRunForNeed = new Function(
  'sanitizeTaskId', `${grab('newestDoneRunForNeed')}; return newestDoneRunForNeed;`
)((id) => String(id == null ? '' : id).replace(/[^\w.-]+/g, '_').slice(0, 60));
// eslint-disable-next-line no-new-func
const artifactRunForNeed = new Function(
  'completedRunForNeed', 'newestDoneRunForNeed',
  `${grab('artifactRunForNeed')}; return artifactRunForNeed;`
)(completedRunForNeed, newestDoneRunForNeed);
// eslint-disable-next-line no-new-func
const needArtifactsButtonHtml = new Function(
  'esc', 'completedTaskForNeed', 'artifactRunForNeed',
  `${grab('hasDeliveryContent')}; ${grab('needArtifactsButtonHtml')}; return needArtifactsButtonHtml;`
)(
  (value) => String(value == null ? '' : value),
  completedTaskForNeed,
  artifactRunForNeed
);

// リトライ中の成果導線: last_run（最新試行）が実行中でも、系統内に done 世代があれば
// 「成果を確認」の導線が残る（リトライで成果が消えたように見えない）
{
  const project = {
    backlog: [{ id: 'T9', status: 'doing', extra: { last_run: 'req-h-T9-r1' } }],
    archive: [],
  };
  const need = { id: 'T9', taskId: 'T9', kind: 'blocked' };
  const runs = [
    { runId: 'req-h-T9-r1', taskId: 'T9', status: 'running', retries: 1 },
    { runId: 'req-h-T9-r0', taskId: 'T9', status: 'done', retries: 0 },
  ];
  assert.strictEqual(completedRunForNeed(project, need, runs), null, '最新試行は未完＝承認根拠にはならない');
  const fallback = artifactRunForNeed(project, need, runs);
  assert.ok(fallback && fallback.runId === 'req-h-T9-r0', '閲覧は系統内の最新 done 世代へフォールバック');
  assert.match(needArtifactsButtonHtml(project, need, runs), /成果を確認/, 'リトライ中も成果導線が残る');
}
// eslint-disable-next-line no-new-func
const runArtifactViewModel = new Function(
  'sanitizeTaskId', `${grab('runArtifactViewModel')}; return runArtifactViewModel;`
)((id) => String(id == null ? '' : id).replace(/[^\w.-]+/g, '_').slice(0, 60));
// eslint-disable-next-line no-new-func
const needArtifactReviewViewModel = new Function(
  'runArtifactViewModel', 'completedTaskForNeed',
  `${grab('needArtifactReviewViewModel')}; return needArtifactReviewViewModel;`
)(runArtifactViewModel, completedTaskForNeed);
// eslint-disable-next-line no-new-func
const runArtifactsButtonHtml = new Function(
  'esc', `${grab('runArtifactsButtonHtml')}; return runArtifactsButtonHtml;`
)((value) => String(value == null ? '' : value));

assert.match(
  runArtifactsButtonHtml({ runId: 'run-done', status: 'done' }),
  /data-run-artifacts="run-done"[^>]*>成果を見る</,
  '完了runから成果ダイアログを開ける'
);
assert.strictEqual(runArtifactsButtonHtml({ runId: 'run-active', status: 'running' }), '');
assert.ok(renderer.includes("querySelectorAll('button[data-run-artifacts]')"), '成果ボタンのクリック配線が必要');
assert.ok(renderer.includes('function openRunArtifacts('), '完了runから成果ダイアログを開く入口が必要');
// eslint-disable-next-line no-new-func
const deliveryReviewFooterHtml = new Function(
  'statusLabel', 'esc', 'isNeedSent', 'needActionsHtml',
  `${grab('deliveryReviewFooterHtml')}; return deliveryReviewFooterHtml;`
)(
  (status) => ({ review: '検収待ち', done: '完了' }[status] || status),
  (value) => String(value == null ? '' : value),
  () => false,
  () => '<button>承認して完了にする</button>'
);
const readOnlyFooter = deliveryReviewFooterHtml({ taskStatus: 'review', readOnly: true });
assert.match(readOnlyFooter, /タスクの状態/);
assert.match(readOnlyFooter, /検収待ち/);
assert.ok(!readOnlyFooter.includes('承認して完了にする'), '成果閲覧からタスク操作を誤って出さない');
// eslint-disable-next-line no-new-func
const deliveryRepoMetaHtml = new Function(
  'esc', `${grab('deliveryRepoMetaHtml')}; return deliveryRepoMetaHtml;`
)((value) => String(value == null ? '' : value));
const branchMeta = deliveryRepoMetaHtml({
  branch: 'ap/T1', target: 'develop', base: 'main', path: '/work/app', url: 'https://git/app.git', role: 'write',
});
for (const expected of ['作業ブランチ', 'ap/T1', 'ターゲット', 'develop', 'ベース', 'main']) {
  assert.ok(branchMeta.includes(expected), `成果の実装情報に「${expected}」が必要`);
}
// eslint-disable-next-line no-new-func
const deliveryDiffOutputFormat = new Function(
  `${grab('deliveryDiffOutputFormat')}; return deliveryDiffOutputFormat;`
)();
assert.strictEqual(deliveryDiffOutputFormat(900), 'side-by-side');
assert.strictEqual(deliveryDiffOutputFormat(375), 'line-by-line', '狭い画面で左右比較を押し込まない');
assert.strictEqual(deliveryDiffOutputFormat(719), 'line-by-line', '差分ペインが狭い場合は行単位表示にする');
assert.strictEqual(deliveryDiffOutputFormat(720), 'side-by-side', '十分な差分ペイン幅でだけ左右比較にする');
assert.match(
  renderer,
  /deliveryDiffOutputFormat\(view\.clientWidth\)/,
  '表示方式はウィンドウ全体ではなく実際の差分ペイン幅で決める'
);
assert.ok(
  renderer.includes('new ResizeObserver(') && renderer.includes('renderDeliveryDiff(view._deliveryDiffText)'),
  'ダイアログを開いたまま幅が変わっても表示方式を再選択する'
);
// eslint-disable-next-line no-new-func
const deliveryDiffRequest = new Function(
  `${grab('deliveryDiffRequest')}; return deliveryDiffRequest;`
)();
assert.strictEqual(
  deliveryDiffRequest({ path: '/work/app', target: 'release', base: 'main', ref: 'origin/ap/T1' }).base,
  'release',
  '検収差分はclone起点のbaseではなく、実際のマージ先targetと比較する'
);

{
  const delivery = [{ name: 'app', role: 'write', branch: 'ap/T1', target: 'develop', base: 'main' }];
  const model = runArtifactViewModel(
    {
      backlog: [{ id: 'T1', status: 'review' }],
      archive: [],
      needs: [{ id: 'T1', taskId: 'T1', title: '成果を確認', delivery, mrUrls: ['https://git/mr/1'] }],
    },
    { runId: 'run-done', taskId: 'T1', status: 'done', final: { summary: '実装完了' } }
  );
  assert.strictEqual(model.readOnly, true);
  assert.strictEqual(model.taskStatus, 'review');
  assert.strictEqual(model.summary, '実装完了');
  assert.deepStrictEqual(model.delivery, delivery, '関連する要確認の構造化成果を優先する');
  assert.deepStrictEqual(model.mrUrls, ['https://git/mr/1']);
}

{
  const model = runArtifactViewModel(
    { workspace: '/work/app', backlog: [], archive: [{ id: 'T1', status: 'done', title: '完了タスク' }], needs: [] },
    {
      runId: 'run-archived',
      taskId: 'T1',
      status: 'done',
      workspace: {
        url: 'https://git.example/app.git',
        desc: 'app',
        base: 'main',
        target: 'develop',
        branch: 'ap/T1',
      },
      gitlabIssues: [{ mergedMrs: [{ web_url: 'https://git.example/app/-/merge_requests/7' }] }],
    }
  );
  assert.deepStrictEqual(model.delivery, [{
    name: 'app',
    role: 'write',
    url: 'https://git.example/app.git',
    path: '/work/app',
    base: 'main',
    target: 'develop',
    branch: 'ap/T1',
    ref: 'ap/T1',
    files: [],
  }]);
  assert.deepStrictEqual(model.mrUrls, ['https://git.example/app/-/merge_requests/7']);
}

{
  const project = {
    workspace: '/work/app',
    backlog: [{ id: 'T1', status: 'blocked' }],
    archive: [],
    needs: [],
  };
  const need = {
    id: 'T1', taskId: 'T1', kind: 'blocked', decided: false,
    failureSummary: '最終検証に失敗しました。', file: '/work/needs/T1.md',
  };
  const model = needArtifactReviewViewModel(project, need, {
    runId: 'run-done', taskId: 'T1', status: 'done',
    workspace: { branch: 'ap/T1', base: 'main' },
  });
  assert.strictEqual(model.readOnly, false, '未承認の検証失敗は差分ダイアログ内で操作できる');
  assert.strictEqual(model.kind, 'blocked');
  assert.strictEqual(model.id, 'T1');
  assert.strictEqual(model.delivery[0].path, '/work/app');
  assert.strictEqual(model.delivery[0].ref, 'ap/T1');
}

{
  const delivery = [{
    name: 'app', role: 'write', path: '/work/app', base: 'main', files: ['src/a.js'],
  }];
  const need = { id: 'T-old', taskId: 'T-old', kind: 'blocked', delivery };
  const model = needArtifactReviewViewModel(
    { backlog: [{ id: 'T-old', status: 'blocked' }], archive: [], needs: [need] },
    need,
    { runId: 'legacy-run', status: 'done' }
  );
  assert.deepStrictEqual(
    model.delivery,
    delivery,
    'taskIdを持たない旧runでも、選択済み要対応の差分情報を検収モデルへ引き継ぐ'
  );
}

assert.deepStrictEqual(
  deliveryReviewState([{ role: 'write', path: '/work/app', files: [] }], []),
  { fileCount: 0, hasMr: false, canDiscover: true, hasContent: false },
  '空のdeliveryエントリを検収物ありと数えず、Gitから確認可能な状態として区別する'
);
assert.strictEqual(
  canDiagnoseNeed({ failureSummary: 'verify failed' }),
  true,
  '失敗情報のある要対応はAI診断できる'
);
assert.strictEqual(
  canDiagnoseNeed({ kind: 'review', why: '検収待ち' }),
  false,
  '失敗情報のない通常検収には診断操作を出さない'
);
// 解析済みの失敗（producer が summary と context を対で出す）はそのまま投影する。
assert.deepStrictEqual(
  needFailureViewModel({
    kind: 'blocked',
    why: '検証コマンド工程で失敗',
    failureSummary: '検証コマンドが失敗しました（終了コード 2）。',
    failureContext: { category: '検証工程', command: 'npm test', exitCode: '2' },
  }),
  {
    summary: '検証コマンドが失敗しました（終了コード 2）。',
    resolution: '',
    context: { category: '検証工程', command: 'npm test', exitCode: '2' },
  },
  '解析済みの失敗要約と根拠をそのまま表示モデルへ運ぶ'
);
// 表示層は散文から失敗要約を作らない。作ると、検証まで到達していない失敗（実行制御による
// 停止・認証切れなど）にも「検証コマンドが失敗しました」と出て、人は走ってもいないテストを
// 探しに行く。解析できなかった失敗は要約を出さず、生の判断材料を読ませる。
assert.strictEqual(
  needFailureViewModel({ kind: 'blocked', why: '検証コマンドが FAIL しました' }),
  null,
  '散文に検証・FAIL があるだけでは検証失敗と断定しない'
);
assert.strictEqual(
  needFailureViewModel({
    kind: 'blocked',
    why: '[agent-error:control] 実行制御（管理設定による停止）',
    detail: '- 検証: `npm test` → 未実行（実行が検証まで到達しなかったため、テストの成否は分かっていません）',
  }),
  null,
  '検証が未実行の記録から検証失敗の要約を作らない'
);
assert.strictEqual(
  needFailureViewModel({ kind: 'blocked', why: 'verify 未定義（工程は完了しています）' }),
  null,
  'verify 未定義の人手確認待ちは検証失敗にしない'
);
assert.strictEqual(
  needListSummary({
    kind: 'blocked',
    failureSummary: 'テストが 2 件失敗しました。',
  }),
  'テストが 2 件失敗しました。',
  '一覧カードは通常の判断文より検証失敗要約を優先する'
);

{
  const nodes = {
    '.master-list': { scrollTop: 420 },
    '.detail-panel': { scrollTop: 180 },
  };
  const root = { querySelector: (selector) => nodes[selector] || null };
  const snapshot = captureNeedsScroll(root);
  assert.deepStrictEqual(snapshot, { list: 420, detail: 180 });
  nodes['.master-list'].scrollTop = 0;
  nodes['.detail-panel'].scrollTop = 0;
  restoreNeedsScroll(root, snapshot, { resetDetail: true });
  assert.strictEqual(nodes['.master-list'].scrollTop, 420, '項目選択後も一覧位置を維持する');
  assert.strictEqual(nodes['.detail-panel'].scrollTop, 0, '新しく選んだ詳細は先頭から表示する');
  restoreNeedsScroll(root, snapshot);
  assert.strictEqual(nodes['.detail-panel'].scrollTop, 180, 'データ更新による再描画では詳細位置も維持する');
  restoreNeedsScroll(root, snapshot, { resetAll: true });
  assert.strictEqual(nodes['.master-list'].scrollTop, 0, 'フィルター切替では一覧を先頭へ戻す');
  assert.strictEqual(nodes['.detail-panel'].scrollTop, 0, 'フィルター切替では詳細も先頭へ戻す');
}
assert.deepStrictEqual(
  deliveryReviewState([{ role: 'write', path: '/work/app', files: ['src/a.js'] }], []),
  { fileCount: 1, hasMr: false, canDiscover: true, hasContent: true },
  '変更ファイルがある場合だけ検収内容ありと判定する'
);

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
  'taskForNeed', 'taskCompletionHint', 'runsForTask', 'canDiagnoseNeed',
  'state', 'needFinalVerificationFailure', 'finalVerificationFailureHtml', 'needAssistActionsHtml',
  'needArtifactsButtonHtml', 'ownerBadgeHtml', 'commandFailureHtml', 'reviewCommentsHtml',
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
  needVerifyRevisionHtml,
  taskForNeed,
  () => null,
  () => [],
  canDiagnoseNeed,
  { flowRuns: [] },
  () => null,
  () => '',
  needAssistActionsHtml,
  needArtifactsButtonHtml,
  () => '',
  () => '',
  () => ''
);
assert.ok(
  renderNeedDetailWithVerifyRevision(
    { backlog: [{ id: 'T1', verify: 'npm test' }] },
    { id: 'T1', title: '検証失敗', kind: 'blocked', decided: false }
  ).includes('need-verify-revision'),
  '検証コマンド変更パネルをblocked要対応の詳細に組み込む'
);
const failureDiagnosisHtml = renderNeedDetailWithVerifyRevision(
  { backlog: [{ id: 'T1', verify: 'npm test' }] },
  { id: 'T1', title: '検証失敗', kind: 'blocked', decided: false, failureSummary: 'テスト失敗' }
);
assert.ok(failureDiagnosisHtml.includes('data-failure-diagnose="T1"'));
assert.ok(failureDiagnosisHtml.includes('AIで失敗を診断'));
assert.ok(!failureDiagnosisHtml.includes('data-need-consult="T1"'), '専用の失敗診断がある場合は汎用AI相談を重複表示しない');
assert.ok(!failureDiagnosisHtml.includes('>AIに相談<'));
assert.match(
  needAssistActionsHtml({ id: 'plain', kind: 'blocked' }, false),
  /data-need-consult="plain"[^>]*>AIに相談</,
  '専用AI操作がない要対応には汎用相談を残す'
);
assert.ok(!needAssistActionsHtml({ id: 'plan', kind: 'plan-review' }, false).includes('data-need-consult'));
assert.ok(!needAssistActionsHtml({ id: 'review', kind: 'review' }, false).includes('data-need-consult'));
assert.ok(renderer.includes("querySelectorAll('button[data-need-consult]')"), '要確認からAI相談を開く配線が必要');
assert.ok(renderer.includes('function openFailureDiagnosis('), '失敗診断を自動開始する入口が必要');
assert.ok(renderer.includes('mode: state.doctorMode'), '追加質問でも診断モードを維持する');

const completedProject = {
  backlog: [],
  archive: [{ id: 'T-DONE', status: 'done', title: '完了済み' }],
};
assert.strictEqual(completedTaskForNeed(completedProject, { id: 'T-DONE' }).status, 'done');
assert.match(
  needArtifactsButtonHtml(completedProject, { id: 'T-DONE' }, []),
  /data-need-artifacts="T-DONE"[^>]*>成果を確認</,
  '完了タスクの要対応詳細から検収ダイアログを開ける'
);
assert.ok(renderer.includes('function openNeedArtifacts('), '要対応項目から成果ダイアログを開く入口が必要');

const doneRunProject = {
  backlog: [{
    id: 'T-VERIFY', status: 'blocked', verify: 'npm test',
    extra: { last_run: 'run-verify-done', needs_reason: '検証コマンドが失敗 exit=2' },
  }],
  archive: [],
};
const verifyFailureNeed = {
  id: 'T-VERIFY', taskId: 'T-VERIFY', kind: 'blocked',
  failureSummary: '検証コマンドが失敗しました（終了コード 2）。',
};
const doneRuns = [{ runId: 'run-verify-done', taskId: 'T-VERIFY', status: 'done' }];
// 承認理由は決定記録として残すだけで、本体の分岐材料にはしない
// （文面から「完了か積み直しか」を推定させない — 意図は complete フラグで送る）。
assert.strictEqual(
  needApprovalReason(doneRunProject, verifyFailureNeed, doneRuns, 'この環境では許容する'),
  'この環境では許容する',
  '人が書いた理由をそのまま決定記録にする'
);
assert.strictEqual(
  needApprovalReason(doneRunProject, verifyFailureNeed, doneRuns, '   '),
  '成果を確認して完了を承認',
  '未記入でも意味のある決定記録を残す'
);
assert.ok(
  !renderer.includes('検証失敗を確認・受容して完了'),
  '本体に完了意図を伝えるための決め打ち文言を承認理由へ混ぜない'
);
assert.match(
  renderer.slice(renderer.indexOf("} else if (act === 'approve') {")).slice(0, 800),
  /action: 'approve'[^)]*complete/s,
  '承認は完了意図（complete）を明示して送る'
);
assert.strictEqual(
  relatedRunIdForNeed(
    { backlog: [{ id: 'T-VERIFY', status: 'blocked', extra: {} }], archive: [] },
    verifyFailureNeed,
    [
      { runId: 'old-failed', taskId: 'T-VERIFY', status: 'failed', createdAt: '2026-07-01' },
      { runId: 'new-done', taskId: 'T-VERIFY', status: 'done', createdAt: '2026-07-02' },
    ]
  ),
  'new-done',
  'last_run が無い場合は同一タスクの最新runを使う'
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
// 長い工程出力は冒頭＋末尾の抜粋にする（全文連結で詳細情報が巨大化しないように）。
const longOutput = `先頭-${'x'.repeat(5000)}-末尾`;
const fullOutput = formatNeedFullOutput(
  { body: 'needs原文' },
  { run: { runId: 'run-last', failureReason: '失敗全体', nodes: { verify: { id: 'verify', goal: '検証', output: longOutput } } }, events: [] }
);
assert.ok(fullOutput.includes('needs原文'));
assert.ok(fullOutput.includes('run-last') && fullOutput.includes('工程 verify'));
assert.ok(!fullOutput.includes(longOutput), '長い工程出力は全文を連結しない');
assert.ok(fullOutput.includes('先頭-') && fullOutput.includes('-末尾'), '冒頭と末尾は残す');
assert.ok(fullOutput.includes('中略'), '省略があることを明示する');
assert.ok(fullOutput.includes('bus/runs/run-last/results/'), '全文の所在を案内する');
// 短い出力はそのまま
const shortOutput = formatNeedFullOutput(
  { body: 'needs原文' },
  { run: { runId: 'r2', nodes: { a: { id: 'a', output: '短い出力' } } }, events: [] }
);
assert.ok(shortOutput.includes('短い出力') && !shortOutput.includes('中略'));

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
assert.ok(renderer.includes('<span>工程完了</span>'), '工程数をタスク完了と区別する');
assert.match(renderer, /class="master-detail/);
assert.match(renderer, /class="flow-detail-shell/);
assert.match(renderer, /data-needs-back/);
assert.match(renderer, /data-flow-back/);
assert.match(css, /\.master-detail/);
assert.match(css, /\.flow-view-tabs/);
assert.match(css, /\.mobile-master-back/);
for (const id of ['btn-doctor', 'dlg-doctor', 'dlg-need-output', 'dlg-delivery-review']) {
  assert.ok(html.includes(`id="${id}"`), `${id} が画面に必要です`);
}
assert.match(html, /<label[^>]+for="doctor-prompt"[^>]*>[^<]*補足したいこと/);
assert.match(html, /<textarea[^>]+id="doctor-prompt"/);
assert.match(html, /id="btn-doctor-submit"[^>]*>相談する</);
assert.ok(!html.match(/id="btn-doctor"[^>]+disabled/), 'AI相談は未選択でも利用可能');
assert.match(html, /id="btn-doctor"[^>]+aria-label="AIに相談"[^>]*>[\s\S]*?<svg[^>]+aria-hidden="true"/);
assert.ok(!html.match(/id="btn-doctor"[^>]*>AI相談<\/button>/), 'AI相談は文字ボタンではなくアイコンにする');
assert.ok(renderer.includes('function openDoctor()'));
assert.ok(renderer.includes('function openPlanCritique('));
assert.ok(renderer.includes('function openDeliveryRationale('));
assert.ok(renderer.includes('function openDeliveryFollowup('));
assert.ok(renderer.includes('function aiEnqueueAssist('));
assert.ok(renderer.includes('function applySelectedEnqueueAdjustments('));
assert.ok(renderer.includes('選択した調整を反映'));
assert.ok(renderer.includes("action: 'revise'"));
assert.ok(renderer.includes('AIで計画を批評'));
assert.ok(renderer.includes('変更理由を説明'));
assert.ok(renderer.includes('フォローアップ案'));
assert.ok(html.includes('AIで依存・優先度を提案'));
assert.ok(html.includes('id="btn-enq-ai"'));
assert.ok(html.includes('id="btn-doctor-apply-feedback"'));
assert.ok(html.includes('id="enq-after-options"'));
assert.ok(html.includes('差し戻し文面を回答欄へ'));
assert.ok(renderer.includes("mode: 'plan-critique'") || renderer.includes("state.doctorMode = 'plan-critique'"));
assert.ok(renderer.includes("mode: 'delivery-rationale'") || renderer.includes("state.doctorMode = 'delivery-rationale'"));
assert.ok(renderer.includes("mode: 'followup-suggest'"));
assert.ok(renderer.includes("mode: 'enqueue-assist'"));
assert.ok(renderer.includes('userPrompt'));
assert.ok(renderer.includes("$('doctor-prompt').disabled = true"));
assert.ok(renderer.includes("$('btn-doctor-submit').disabled = true"));
assert.ok(!html.includes('id="btn-git-heal"'), '同期修復は Doctor の固定操作にしない');
assert.ok(renderer.includes('id="btn-sync-now"'), '同期状態の横に文脈付き操作を表示する');
for (const cli of ['kiro', 'claude', 'copilot', 'codex', 'cursor', 'ollama']) {
  assert.ok(renderer.includes(`<option value="${cli}"`), `${cli} を設定で選択できる必要があります`);
}
assert.ok(renderer.includes('詳細情報を開く'));
assert.ok(renderer.includes('検収物を確認'));
assert.ok(renderer.includes('openDeliveryReview'));
assert.ok(renderer.includes('すべての差分を表示'));
assert.ok(renderer.includes('data-delivery-all-diff'));
assert.ok(renderer.includes('function renderDeliveryDiff('), 'diff2html の描画入口が必要です');
assert.ok(renderer.includes('function hydrateDeliveryEntries('), 'Gitから完全なファイル一覧を取得する');
assert.ok(renderer.includes('new Diff2HtmlUI('), 'diff2html を使って差分を描画する');
assert.ok(renderer.includes('data-delivery-file'), '変更ファイルを選択できる必要があります');
assert.ok(renderer.includes('delivery-review-actions'), '差分を見ながら検収操作できる必要があります');
assert.ok(renderer.includes('needActionsHtml(need, { inReview: true })'), '承認・差し戻し操作は検収画面内に出す');
assert.ok(
  !renderer.includes('class="delivery-artifact-summary"'),
  '成果runの長い概要でファイル一覧・差分領域を画面外へ押し出さない'
);
assert.ok(
  renderer.indexOf('state.flowRuns = fr.runs || []') < renderer.indexOf('state.project = project'),
  'project と完了runは同時に表示状態へ反映し、読込途中に承認判定を外さない'
);
assert.ok(html.includes('../../node_modules/diff2html/bundles/css/diff2html.min.css'));
assert.ok(html.includes('../../node_modules/diff2html/bundles/js/diff2html-ui-slim.min.js'));
assert.ok(renderer.includes('workingTree: !entry.ref && !entry.branch'),
  'ref も branch も無いときだけ作業ツリー比較（branch があれば origin/<branch> で range 比較）');
assert.ok(renderer.includes('branch: entry.branch') && renderer.includes('fetch: Boolean(opts && opts.fetch)'),
  '検収差分は branch を渡し、fetch 後は origin/<branch> を優先する');
assert.ok(renderer.includes("deliveryDiffRequest(entry, '', { fetch: true })"),
  '検収を開いた最初の解決でリモートを取り込む（古い diff が出ない）');
assert.match(css, /\.nav-group/);
assert.match(css, /\.doctor-form/);
assert.match(css, /\.delivery-dialog/);
assert.match(css, /\.delivery-review-layout/);
assert.match(css, /\.delivery-file-button/);
assert.match(css, /\.delivery-review-actions/);
assert.match(
  css,
  /\.delivery-review-actions\s+\.need-actions[\s\S]*?display:\s*flex[\s\S]*?flex-direction:\s*column/
);
assert.match(
  css,
  /\.delivery-review-actions\s+\.need-buttons[^{]*\{[^}]*order:\s*-1/
);
assert.match(css, /grid-template-rows:\s*auto minmax\(0, 1fr\) clamp\(/);
assert.match(css, /\.flow-outcome-status\s*\{/);
assert.match(
  css,
  /\.delivery-diff-view\s+\.d2h-wrapper[\s\S]*?max-width:\s*100%/,
  'Diff2Htmlの内側要素を成果ペイン幅へ収める'
);
for (const stateClass of ['', '.d2h-info', '.d2h-del', '.d2h-ins', '.d2h-del.d2h-change', '.d2h-ins.d2h-change']) {
  const escaped = stateClass.replaceAll('.', '\\.');
  assert.match(
    css,
    new RegExp(`\\.delivery-diff-view\\s+\\.d2h-code-side-linenumber${escaped}\\s*\\{[^}]*background(?:-color)?:\\s*#[0-9a-fA-F]{6}`),
    `左右比較の行番号列 ${stateClass || '通常'} は下のコードが透けない不透明背景にする`
  );
}
assert.match(
  css,
  /\.delivery-diff-view\s+\.d2h-files-diff\s+\.d2h-diff-tbody\s+tr\s*\{[^}]*position:\s*relative/,
  '左右比較の各コード行を行番号列の配置基準にする'
);
assert.match(
  css,
  /\.delivery-diff-view\s+\.d2h-code-side-linenumber\s*\{[^}]*inset-block:\s*0[^}]*height:\s*auto/,
  '行番号列をコード行の上端から下端まで伸ばす'
);

// verify 未定義の確認待ち（blocked）は「承認して完了にする」を出す（承認で done 確定）
{
  // eslint-disable-next-line no-new-func
  const isVerifyPendingNeed = new Function(
    `${grab('taskForNeed')}; ${grab('isVerifyPendingNeed')}; return isVerifyPendingNeed;`
  )();
  const project = {
    backlog: [
      { id: 'T1', status: 'blocked', verify: '', extra: { needs_reason: 'verify 未定義（工程は完了しています…）' } },
      { id: 'T2', status: 'blocked', verify: 'npm test', extra: { needs_reason: '繰り返し NG' } },
      { id: 'T3', status: 'blocked', verify: '', extra: { env_resume: '1', needs_reason: '[agent-error:env] verify 未定義' } },
    ],
  };
  const actionState = { project, flowRuns: [] };
  assert.strictEqual(
    isVerifyPendingNeed(project, { id: 'T1', taskId: 'T1', kind: 'blocked', why: 'verify 未定義（工程は完了しています…）' }),
    true
  );
  assert.strictEqual(
    isVerifyPendingNeed(project, { id: 'T2', taskId: 'T2', kind: 'blocked', why: '検証が失敗' }),
    false,
    'verify があるタスクは対象外'
  );
  assert.strictEqual(
    isVerifyPendingNeed(project, { id: 'T3', taskId: 'T3', kind: 'blocked', why: '[agent-error:env] verify 未定義' }),
    false,
    '環境要因（env_resume）は続きから再開の契約なので対象外'
  );
  assert.strictEqual(
    isVerifyPendingNeed(project, { id: 'T1', taskId: 'T1', kind: 'review', why: 'verify 未定義' }),
    false,
    'blocked 以外の票には出さない'
  );

  // eslint-disable-next-line no-new-func
  // eslint-disable-next-line no-new-func
  const needHasDeliverable = new Function(
    'isVerifyPendingNeed', 'completedTaskForNeed', 'artifactRunForNeed', 'taskForNeed',
    `${grab('needHasDeliverable')}; return needHasDeliverable;`
  )(isVerifyPendingNeed, completedTaskForNeed, artifactRunForNeed, taskForNeed);
  // eslint-disable-next-line no-new-func
  const needActionsHtml = new Function(
    'esc', 'state', 'milestoneStatusFor', 'milestoneVersionName',
    'statusLabel', 'needCompleteHowHtml', 'needHasDeliverable', 'orchBlockedBannerHtml',
    `${grab('needActionsHtml')}; return needActionsHtml;`
  )(
    (value) => String(value == null ? '' : value),
    actionState,
    () => null,
    () => null,
    (status) => String(status || ''),
    () => '',
    needHasDeliverable,
    () => ''            // 実行制御は稼働中＝警告なし（停止時の文言は orchestration-blocker で検証）
  );
  const pendingHtml = needActionsHtml({
    id: 'T1', taskId: 'T1', kind: 'blocked', why: 'verify 未定義（工程は完了しています…）', file: '/p/needs/T1.md',
  });
  assert.match(pendingHtml, /data-act="approve"[^>]*>承認して完了にする</, 'verify 未定義の確認待ちに承認完了ボタンを出す');
  assert.match(pendingHtml, /data-act="rerun"/, 'そのまま再実行も残す');
  const plainHtml = needActionsHtml({
    id: 'T2', taskId: 'T2', kind: 'blocked', why: '検証が失敗', file: '/p/needs/T2.md',
  });
  assert.ok(!plainHtml.includes('data-act="approve"'), '通常の blocked に承認完了ボタンを出さない');

  // 検収物が少しでもあれば、要対応の画面から直接「承認して完了にする」を押せる。
  // 以前は差分検収ダイアログ経由に限定していたが、経路が絞られるほど
  // 「操作が見当たらない」状態が生まれるため、承認は常に出す方針へ変えた。
  actionState.project = doneRunProject;
  actionState.flowRuns = doneRuns;
  const completedFailureHtml = needActionsHtml(verifyFailureNeed);
  assert.match(
    completedFailureHtml,
    /data-act="approve"[^>]*>承認して完了にする</,
    '工程完了済みの検証失敗は要対応の画面から直接完了承認できる'
  );
  assert.match(
    needActionsHtml(verifyFailureNeed, { inReview: true }),
    /data-act="approve"[^>]*>承認して完了にする</,
    '差分検収ダイアログ内でも同じ承認ができる'
  );

  // 「少しでも検収物があれば承認できる」— 以前は AND 連鎖のどれか 1 つが欠けると
  // 承認ボタンごと消えていた。欠けがちだった条件を 1 つずつ落として確認する。
  const runsMissing = { ...doneRunProject };
  assert.strictEqual(
    needHasDeliverable(runsMissing, verifyFailureNeed, []),
    true,
    'run 一覧が未読み込みでも、タスクの last_run があれば承認できる'
  );
  assert.strictEqual(
    needHasDeliverable(
      { backlog: [{ id: 'T-VERIFY', status: 'doing', extra: { last_run: 'run-verify-done' } }], archive: [] },
      verifyFailureNeed,
      doneRuns
    ),
    true,
    'タスクの状態が blocked でなくても、成果があれば承認できる'
  );
  assert.strictEqual(
    needHasDeliverable(
      { backlog: [{ id: 'T-VERIFY', status: 'blocked', extra: {} }], archive: [] },
      { id: 'T-VERIFY', taskId: 'T-VERIFY', kind: 'blocked' },
      doneRuns
    ),
    true,
    '失敗内容を解析できなくても、完了した run があれば承認できる'
  );
  assert.strictEqual(
    needHasDeliverable(
      { backlog: [{ id: 'T-NEW', status: 'blocked', extra: {} }], archive: [] },
      { id: 'T-NEW', taskId: 'T-NEW', kind: 'blocked', why: '着手前に失敗' },
      []
    ),
    false,
    '成果が一度も出ていないものは完了承認を出さない（再開・指示が正しい導線）'
  );
}

console.log('detail-tabs-ui: all tests passed');
