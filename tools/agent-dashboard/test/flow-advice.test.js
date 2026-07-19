'use strict';

// フロー画面の runAdvice（「次に何が起きるか・あなたの出番はあるか」を言い切る判定）の検証。
// 追加依存なしで `node test/flow-advice.test.js` で走る。
//
// 背景: 同じ「応答なし」でも正解の行動が違う —
//   本体が稼働中でタスクが ready なら「放置すれば自動再開」、
//   needs 待ちなら「要対応タブで回答」（ここで待っても押しても動かない）、
//   タスクに紐づかない run なら「ボタンでやり直す」。
// この判定を UI の第一言語にする以上、判定自体が仕様なのでテストで固定する。
//
// renderer.js は DOM 前提の単一スクリプトなので、判定に必要な関数だけを
// ソースから抽出して隔離実行する（判定は state と引数のみに依存する純関数）。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- renderer.js から関数を抽出（ブレース対応で本体を切り出す） ---------------
const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');

function grab(name) {
  const at = src.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let i = src.indexOf('{', at);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

// 判定器を隔離された環境で組み立てる。state は呼び出しごとに差し替える。
function makeAdvisor(project) {
  const code = [
    'const TERMINAL_RUN_STATES = new Set(["done", "failed", "canceled"]);',
    'const statusLabel = (s) => ({ review: "検収待ち", blocked: "要対応", proposed: "計画承認待ち" }[s] || s);',
    grab('sanitizeTaskId'),
    grab('shortRunId'),
    grab('taskOfRun'),
    grab('humanWaitingAdvice'),
    grab('agentErrorAdvice'),
    grab('runAdvice'),
    'return runAdvice;',
  ].join('\n');
  // eslint-disable-next-line no-new-func
  return new Function('state', code)({ project });
}

function makeNodePresenter(project) {
  const code = [
    'const TERMINAL_RUN_STATES = new Set(["done", "failed", "canceled"]);',
    'const statusLabel = (s) => ({ review: "検収待ち", blocked: "要対応", proposed: "計画承認待ち" }[s] || s);',
    'const esc = (s) => String(s == null ? "" : s);',
    grab('sanitizeTaskId'),
    grab('shortRunId'),
    grab('taskOfRun'),
    grab('humanWaitingAdvice'),
    grab('agentErrorAdvice'),
    grab('runAdvice'),
    grab('flowRetryUi'),
    grab('nodeFateLine'),
    'return { runAdvice, flowRetryUi, nodeFateLine };',
  ].join('\n');
  // eslint-disable-next-line no-new-func
  return new Function('state', code)({ project });
}

// --- テストデータ --------------------------------------------------------------
const RID = 'req-abcd1234-T-9-r1';
const baseRun = (over = {}) => ({
  runId: RID,
  taskId: 'T-9',
  status: 'running',
  alive: true,
  archived: false,
  counts: { done: 5, failed: 1, claimed: 0, pending: 2, waiting: 0 },
  ...over,
});
const group = (latest, attempts = [latest]) => ({ latest, attempts });
const project = ({ taskStatus = 'ready', lastRun = RID, running = true, paused = false,
  via = undefined, ageSec = undefined } = {}) => ({
  liveness: { running, paused, via, ageSec },
  needs: [],
  archive: [],
  backlog: taskStatus === null ? [] : [
    { id: 'T-9', status: taskStatus, extra: lastRun ? { last_run: lastRun } : {} },
  ],
});

// --- 仕様 -----------------------------------------------------------------------
test('実行中（lease 生存）→ 見守るだけ', () => {
  const advise = makeAdvisor(project());
  const r = baseRun();
  assert.strictEqual(advise(r, group(r)).kind, 'watch');
});

test('応答なし + タスク ready + 本体稼働中 → 自動でやり直される（操作不要）', () => {
  const advise = makeAdvisor(project({ running: true }));
  const r = baseRun({ alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'auto');
  assert.match(a.text, /操作は不要/);
});

test('応答なし + タスク ready + 本体停止中 → 起動ボタンでその場で解決できる（restart）', () => {
  const advise = makeAdvisor(project({ running: false }));
  const r = baseRun({ alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'restart');
  assert.strictEqual(a.stopped, true);           // バナーに「自動実行を開始」が出る
  assert.match(a.text, /自動実行を開始/);
});

test('一時停止中 → 再開ボタンでその場で解決できる', () => {
  const advise = makeAdvisor(project({ running: true, paused: true }));
  const r = baseRun({ status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'restart');
  assert.strictEqual(a.stopped, false);          // バナーに「▶ 再開」が出る
  assert.match(a.text, /一時停止中/);
});

test('失敗 + タスクが review（判断待ち）→ 要対応タブへ誘導（押しても動かないことを言う）', () => {
  const advise = makeAdvisor(project({ taskStatus: 'review' }));
  const r = baseRun({ status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.text, /要対応/);
  assert.match(a.chip, /検収待ち/);
  assert.strictEqual(a.taskId, 'T-9');
});

test('判断待ちの失敗工程 → 存在しない再実行ボタンを案内せず要対応へ誘導する', () => {
  const presenter = makeNodePresenter(project({ taskStatus: 'review' }));
  const r = baseRun({ status: 'failed', alive: false });
  const advice = presenter.runAdvice(r, group(r));
  const retry = presenter.flowRetryUi(r, advice);
  assert.strictEqual(retry.show, false);
  const line = presenter.nodeFateLine(r, 'failed', retry, advice);
  assert.match(line, /要対応/);
  assert.ok(!line.includes('失敗した工程だけやり直す'), '非表示のボタン名を案内しない');
});

test('再実行できる失敗工程 → ボタンがある「概要」タブを明示する', () => {
  const presenter = makeNodePresenter(project({ taskStatus: null }));
  const r = baseRun({ taskId: null, status: 'failed', alive: false });
  const advice = presenter.runAdvice(r, group(r));
  const retry = presenter.flowRetryUi(r, advice);
  assert.strictEqual(retry.show, true);
  const line = presenter.nodeFateLine(r, 'failed', retry, advice);
  assert.match(line, /概要/);
  assert.ok(line.includes(retry.label));
});

test('done + タスクが review（検収待ち）→ 完了扱いにせず要確認へ（delivery_review）', () => {
  // verify=PASS 後に delivery_review でタスクが review に残ると、run 自体は done になる。
  // run.status=done を先に見ると「完了」扱いで操作待ちから消えるバグの回帰防止。
  const advise = makeAdvisor(project({ taskStatus: 'review' }));
  const r = baseRun({ status: 'done', alive: false, counts: { done: 5, failed: 0, claimed: 0, pending: 0, waiting: 0 } });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.chip, /検収待ち/);
  assert.match(a.text, /検収|承認/);
  assert.strictEqual(a.taskId, 'T-9');
});

test('archived + タスクが review → 記録扱いにせず検収待ちを示す', () => {
  const advise = makeAdvisor(project({ taskStatus: 'review' }));
  const r = baseRun({ status: 'done', archived: true, alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.chip, /検収待ち/);
});

test('実行中だが parked（承認待ち）→ 見守るだけでなく人の番として示す', () => {
  const advise = makeAdvisor(project({ taskStatus: 'offloaded' }));
  const r = baseRun({
    status: 'running', alive: true,
    counts: { done: 2, failed: 0, claimed: 0, pending: 0, waiting: 0, parked: 1 },
  });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.chip, /承認待ち/);
});

test('古い試行（最新が別 run）→ 見るだけ・削除可、最新への誘導', () => {
  const advise = makeAdvisor(project());
  const r = baseRun({ status: 'failed', alive: false });
  const latest = baseRun({ runId: 'req-abcd1234-T-9-r2' });
  const a = advise(r, { latest, attempts: [latest, r] });
  assert.strictEqual(a.kind, 'old');
  assert.strictEqual(a.latestId, 'req-abcd1234-T-9-r2');
  assert.match(a.text, /削除しても安全/);
});

test('タスクが archive（完了済み）→ この run は記録（操作不要）', () => {
  const p = project({ taskStatus: null });
  p.archive = [{ id: 'T-9', status: 'done', extra: {} }];
  const advise = makeAdvisor(p);
  const r = baseRun({ status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'none');
  assert.match(a.text, /完了しています/);
});

test('タスクに紐づかない失敗 run → あなたの操作待ち（自動では再開されない）', () => {
  const advise = makeAdvisor(project({ taskStatus: null }));
  const r = baseRun({ taskId: null, status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'manual');
  assert.match(a.text, /自動では再開されません/);
});

test('done / 記録（archived）→ 操作なしを明言', () => {
  const advise = makeAdvisor(project());
  assert.strictEqual(advise(baseRun({ status: 'done' }), null).kind, 'none');
  assert.strictEqual(advise(baseRun({ archived: true }), null).kind, 'none');
});

test('本体稼働中の advice は「別の作業中なら順番に実行」と言う（まもなく、と言い切らない）', () => {
  const advise = makeAdvisor(project({ running: true }));
  const r = baseRun({ status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'auto');
  assert.match(a.text, /順番に/);
});

test('別マシンの応答途絶は「停止」と言い切らず、↻ が予約になることを言う', () => {
  // 別マシンの本体は長い作業中 status.json を更新できない＝停止と断定できない
  const advise = makeAdvisor(project({ running: false, via: 'status-sync', ageSec: 720 }));
  const r = baseRun({ status: 'failed', alive: false });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'restart');
  assert.match(a.chip, /別マシン/);
  assert.match(a.text, /12 分前/);
  assert.match(a.text, /予約/);
  assert.ok(!/停止中です/.test(a.text), '停止と言い切らない');
});

test('失敗トリアージ: 認証切れタグ → タスク状態より先に「何を直すか」を言い切る', () => {
  // 本体が稼働中 + タスク ready なら普段は「自動でやり直される」だが、認証が切れている限り
  // 自動やり直しも同じ理由で落ちる。環境の修復が先、と言い切る。
  const advise = makeAdvisor(project({ running: true }));
  const r = baseRun({
    status: 'failed', alive: false,
    failureReason: '[agent-error:auth] 環境要因の失敗（t2）: 認証に失敗しています',
  });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.chip, /認証切れ/);
  assert.match(a.text, /再ログイン/);
  assert.match(a.text, /温存/);
});

test('失敗トリアージ: blocked + auth → 判断待ちより環境要因を優先する', () => {
  // agent-error で blocked になったあと、humanWaitingAdvice が先だと「判断待ち」に誤誘導する。
  const advise = makeAdvisor(project({ taskStatus: 'blocked' }));
  const r = baseRun({
    status: 'failed', alive: false,
    failureReason: '[agent-error:auth] 環境要因の失敗（t2）: 認証に失敗しています',
  });
  const a = advise(r, group(r));
  assert.strictEqual(a.kind, 'human');
  assert.match(a.chip, /認証切れ/);
  assert.ok(!/あなたの判断待ち/.test(a.chip), '汎用の判断待ちで上書きしない');
});

test('失敗トリアージ: 利用上限タグ → 時間をおけば回復と言う', () => {
  const advise = makeAdvisor(project());
  const r = baseRun({
    status: 'failed', alive: false,
    failureReason: '[agent-error:quota] 環境要因の失敗（t1）: 利用上限',
  });
  const a = advise(r, group(r));
  assert.match(a.chip, /利用上限/);
  assert.match(a.text, /時間をおく/);
});

test('失敗トリアージ: agent-control stop → AI 利用上限ではなく実行停止と言う', () => {
  // 旧 run の failure_reason は quota の汎用文言まで丸められている。
  // ノード出力に残る発生元タグから復元し、既存 run も正しく案内する。
  const advise = makeAdvisor(project());
  const r = baseRun({
    status: 'failed', alive: false,
    failureReason: '[agent-error:quota] 環境要因の失敗（t1）: 利用上限',
    nodes: {
      t1: { output: '[agent-error:quota] [agent-control] ワークロード（flow）は lifecycle=stop 指定です' },
    },
  });
  const a = advise(r, group(r));
  assert.match(a.chip, /実行停止中/);
  assert.match(a.text, /AI の利用上限ではありません/);
  assert.match(a.text, /run/);
  assert.ok(!/時間をおく/.test(a.text), '時間経過で回復すると誤案内しない');
});

test('失敗トリアージ: node-budget → 外部 AI ではなくノード予算と言う', () => {
  const advise = makeAdvisor(project());
  const r = baseRun({
    status: 'failed', alive: false,
    failureReason: '[agent-error:quota] [node-budget] このノードのトークン予算を超過しています',
  });
  const a = advise(r, group(r));
  assert.match(a.chip, /ノード予算/);
  assert.match(a.text, /AI サービス側の利用上限ではありません/);
  assert.match(a.text, /オーケストレーション/);
});

console.log(`\n${passed} passed`);
