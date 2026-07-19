'use strict';

// 実行制御（control.json の lifecycle）で止まっているときの事前検査と、
// 「そのまま再実行」の経路選択を検証する。
//
// 背景: 管理面が flow/project を stop にしたまま要対応の再実行・承認を押すと、本体は着手前に
// [agent-control] で弾く。その失敗が上流で quota などに誤分類されると、画面は「利用上限です・
// 時間をおいてください」と表示し、人は永久に回復しない待ちに入る（実際そうなっていた）。
//   - orchBlockedWorkloads / orchBlockedBannerHtml: 押す前に「送っても動かない」を出す
//   - needRerunPlan: 再実行を resume-run（指示ファイルが残る正規の口）へ寄せる

const assert = require('assert');

const renderer = require('./helpers/renderer-src').read();

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = at + `function ${name}`.length;
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

function test(name, fn) {
  fn();
  console.log(`  ok ${name}`);
}

// 依存（state / esc / orchLifecycleLabel / ワークロード定数）を注入して評価する。
function makeOrch(control) {
  const preamble = `
    const state = ${JSON.stringify({ orchestration: control })};
    function esc(s) { return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
    const ORCH_BLOCKING_WORKLOADS = ['project', 'flow'];
  `;
  // eslint-disable-next-line no-new-func
  return new Function(`${preamble}
    ${grab('orchLifecycleLabel')}
    ${grab('orchBlockedWorkloads')}
    ${grab('orchBlockedBannerHtml')}
    return { orchBlockedWorkloads, orchBlockedBannerHtml };`)();
}

// eslint-disable-next-line no-new-func
const needRerunPlan = new Function(
  `${grab('taskForNeed')}; ${grab('needRerunPlan')}; return needRerunPlan;`
)();

console.log('orchestration-blocker');

test('lifecycle が run なら何も警告しない', () => {
  const o = makeOrch({ control: { workloads: { project: { lifecycle: 'run' }, flow: { lifecycle: 'run' } } } });
  assert.deepStrictEqual(o.orchBlockedWorkloads(), []);
  assert.strictEqual(o.orchBlockedBannerHtml(), '');
});

test('lifecycle 未指定は run 扱い（設定前の端末で警告を出さない）', () => {
  const o = makeOrch({ control: { workloads: {} } });
  assert.deepStrictEqual(o.orchBlockedWorkloads(), []);
  assert.strictEqual(o.orchBlockedBannerHtml(), '');
});

test('orchestration が未取得でも壊れない', () => {
  assert.deepStrictEqual(makeOrch(null).orchBlockedWorkloads(), []);
  assert.deepStrictEqual(makeOrch({}).orchBlockedWorkloads(), []);
});

test('stop / pause のワークロードを検出する', () => {
  const o = makeOrch({ control: { workloads: { project: { lifecycle: 'stop' }, flow: { lifecycle: 'pause' } } } });
  assert.deepStrictEqual(o.orchBlockedWorkloads(), [
    { workload: 'project', lifecycle: 'stop' },
    { workload: 'flow', lifecycle: 'pause' },
  ]);
});

test('片方だけ止まっていても検出する（flow=stop で実行は進まない）', () => {
  const o = makeOrch({ control: { workloads: { project: { lifecycle: 'run' }, flow: { lifecycle: 'stop' } } } });
  assert.deepStrictEqual(o.orchBlockedWorkloads(), [{ workload: 'flow', lifecycle: 'stop' }]);
});

test('警告は状態語・原因・戻す導線を含む（色だけに頼らない）', () => {
  const o = makeOrch({ control: { workloads: { flow: { lifecycle: 'stop' } } } });
  const html = o.orchBlockedBannerHtml();
  assert.ok(html.includes('flow = 停止'), '状態を日本語の状態語で示す');
  assert.ok(/同じ要対応に戻ります/.test(html), '送っても動かないことを明示する');
  assert.ok(html.includes('data-orch-open'), 'エージェント管理へ戻す導線を出す');
});

test('再実行は last_run がある票を resume-run へ送る', () => {
  const project = { backlog: [{ id: 't-1', extra: { last_run: 'req-abc-t-1-r0' } }] };
  assert.deepStrictEqual(needRerunPlan(project, { id: 't-1' }), {
    via: 'resume-run', id: 't-1', run: 'req-abc-t-1-r0',
  });
});

test('再実行は taskId 経由の票でも run を引ける', () => {
  const project = { backlog: [{ id: 't-1', extra: { last_run: 'req-abc-t-1-r0' } }] };
  assert.deepStrictEqual(needRerunPlan(project, { id: 'need-9', taskId: 't-1' }), {
    via: 'resume-run', id: 't-1', run: 'req-abc-t-1-r0',
  });
});

test('再開できる run が無ければ従来の feedback 経路へ落とす', () => {
  assert.deepStrictEqual(
    needRerunPlan({ backlog: [{ id: 't-1', extra: {} }] }, { id: 't-1' }), { via: 'feedback' }
  );
  assert.deepStrictEqual(
    needRerunPlan({ backlog: [{ id: 't-1', extra: { last_run: '  ' } }] }, { id: 't-1' }), { via: 'feedback' }
  );
  assert.deepStrictEqual(needRerunPlan({ backlog: [] }, { id: 'synth-1' }), { via: 'feedback' });
  assert.deepStrictEqual(needRerunPlan(null, null), { via: 'feedback' });
});

// --- 成果物の有無（症状3） -------------------------------------------------------------
// 「人が完了を選べるか」（needHasDeliverable）と「成果物が実際にあるか」（needHasArtifacts）を
// 1 つの述語で兼ねていたため、実行を 1 回試したことしか示さない last_run が成果の根拠に使われ、
// 着手前に止まって成果物ゼロの票にも「成果はできています。」と表示していた。
const artifacts = (() => {
  const preamble = `
    function isVerifyPendingNeed() { return false; }
    function completedTaskForNeed() { return null; }
    function artifactRunForNeed(p, n, runs) { return (runs || [])[0] || null; }
  `;
  // eslint-disable-next-line no-new-func
  return new Function(`${preamble}
    ${grab('taskForNeed')}
    ${grab('needHasDeliverable')}
    ${grab('needHasArtifacts')}
    return { needHasDeliverable, needHasArtifacts };`)();
})();

const ranButEmpty = {
  project: { backlog: [{ id: 't-1', extra: { last_run: 'req-abc-t-1-r0' } }] },
  need: { id: 't-1', kind: 'blocked', delivery: [{ files: [], mr_url: '' }], diff: null },
};

test('実行しただけの last_run を成果物の根拠にしない', () => {
  assert.strictEqual(
    artifacts.needHasArtifacts(ranButEmpty.project, ranButEmpty.need, []), false,
    '成果物ゼロなら「成果あり」と言わない'
  );
  assert.strictEqual(
    artifacts.needHasDeliverable(ranButEmpty.project, ranButEmpty.need, []), true,
    '完了を選ぶ導線は残す（見せるものが無くても人は完了を選べる）'
  );
});

test('実データがあれば成果物ありと判定する', () => {
  const p = { backlog: [{ id: 't-1', extra: {} }] };
  assert.strictEqual(
    artifacts.needHasArtifacts(p, { id: 't-1', kind: 'blocked', delivery: [{ files: ['src/a.js'] }] }, []),
    true, '変更ファイルがあれば成果物あり'
  );
  assert.strictEqual(
    artifacts.needHasArtifacts(p, { id: 't-1', kind: 'blocked', delivery: [{ files: [], mr_url: 'https://x/1' }] }, []),
    true, 'MR があれば成果物あり'
  );
  assert.strictEqual(
    artifacts.needHasArtifacts(
      p, { id: 't-1', kind: 'blocked', diff: { hasDiff: true, artifacts: ['src/a.js'] } }, []),
    true, '差分に成果物が並んでいれば成果物あり'
  );
  assert.strictEqual(
    artifacts.needHasArtifacts(p, { id: 't-1', kind: 'blocked' }, [{ status: 'done' }]),
    true, '完了 run があれば成果物あり'
  );
});

test('ref 未解決で差分を取れない票は「無い」と断定しない', () => {
  // 取得できないだけで、成果物が無いとは限らない。断定を避けて確認へ回す。
  const p = { backlog: [{ id: 't-1', extra: { last_run: 'r0' } }] };
  const need = { id: 't-1', kind: 'blocked', delivery: [{ files: [], ref: '', branch: 'ap/t-1' }] };
  assert.strictEqual(artifacts.needHasArtifacts(p, need, []), false);
  assert.strictEqual(artifacts.needHasDeliverable(p, need, []), true);
});

console.log('orchestration-blocker: ok');
