'use strict';

// coherence: code=tools/agent-dashboard/src/features/adhoc-flow/main/reuse.js, doc=docs/plans/2026-08-31-agent-session-reuse-rerun-design.md

// 過去セッション・run の流用実行（編集付き再実行 / テンプレート化 / map・反復）の単体テスト。
// Electron は起動しない。追加依存なしで `node test/reuse-rerun.test.js` で走る。
//
// 護るもの:
//   §1 fork    … 入力を編集した再実行は世代交代（inherit_from）ではなく分岐。旧 run は残り、
//                何を変えたかが edited_fields に残る。
//   §2/§3 蒸留 … 種は run / セッション。保存形には複製元 `source` が付き、transcript 本文は
//                inbox にも保存形にも入らない。
//   §4 変数    … `{{key}}` の検出は定常業務と同じ 1 実装。予約語は入力扱いしない。
//                未入力・未定義キーは投函前に断る（`{{key}}` のまま実行させない）。
//   §5 バッチ  … 件数上限と確認を通らないと投函できない。行ごとに workspace を持つ。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

process.env.AGENT_TUNING_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'reuse-tuning-'));

const templateParameters = require('../src/base/main/template-parameters');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const reuse = require('../src/features/adhoc-flow/main/reuse');
const cowork = require('../src/features/cowork/main/cowork');
const exec = require('../src/features/routines/main/exec');
const parameterFields = require('../src/renderer/parameter-fields');
const workflowUi = require('../src/renderer/features/adhoc-flow');
const auditUi = require('../src/renderer/features/agent-audit');

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function withStubbedExec(fn, shell) {
  const original = exec.shInWsl;
  exec.shInWsl = shell || (() => ({ ok: true, status: 0, stdout: 'launched:1', stderr: '', error: '' }));
  try { return fn(); } finally { exec.shInWsl = original; }
}

// 共有リモートを持つ Git フォルダを装う shInWsl。gitWorkspace と起動行の両方を通す。
function gitAwareShell(roots) {
  return (line) => {
    const match = /rev-parse --show-toplevel/.test(line)
      && Object.keys(roots).find((root) => line.includes(root));
    if (/rev-parse --show-toplevel/.test(line)) {
      return match
        ? { status: 0, stdout: `${match}\nmain\n${roots[match]}`, stderr: '' }
        : { status: 2, stdout: '', stderr: '' };
    }
    return { status: 0, stdout: 'launched:1', stderr: '' };
  };
}

// --- §4 変数（検出・置換の 1 実装） -----------------------------------------

test('`{{key}}` の検出は定常業務と同じ 1 実装を共有する', () => {
  // cowork が公開している検出関数が、共有モジュールと同一の関数であること
  // （別実装を持たせると「画面が拾ったキー」と「main が要求するキー」がずれる）。
  assert.strictEqual(cowork.templateParameterKeys, templateParameters.templateParameterKeys);
  assert.strictEqual(cowork.validateParameters, templateParameters.validateParameters);
  assert.strictEqual(cowork.applyParameters, templateParameters.applyParameters);
});

test('予約語は入力パラメータにしない（{{request}}・statemachine の組み込み変数）', () => {
  const keys = templateParameters.inputParameterKeys(
    '{{request}} を {{repo}} で行い、{{today}} に報告する。{{repo}} は 1 項目。');
  assert.deepStrictEqual(keys, ['repo']);
});

test('置換は値のあるキーだけ。予約語はエンジンのために残す', () => {
  const filled = templateParameters.applyParameters('{{request}} を {{repo}} で', { repo: 'sandbox' });
  assert.strictEqual(filled, '{{request}} を sandbox で');
});

test('保存形テンプレートの入力パラメータは goal と要求文から拾う', () => {
  const workflow = {
    nodes: [{ id: 'a', goal: '{{repo}} の {{target}} を直す' }, { id: 'b', goal: '{{request}} を検証' }],
  };
  assert.deepStrictEqual(adhoc.workflowParameterKeys(workflow, '{{owner}} へ報告'),
    ['repo', 'target', 'owner']);
});

test('画面の入力欄も 1 実装（必須の文字列入力だけ）', () => {
  const html = parameterFields.fieldsHtml(['repo', 'target'], { prefix: 'p' });
  assert.match(html, /data-parameter="repo"/);
  assert.match(html, /type="text"\s+autocomplete="off" required/);
  assert.strictEqual(parameterFields.fieldsHtml([]), '');
});

// --- §4 投入時のフェイルクローズ ---------------------------------------------

test('未入力の `{{key}}` は投函前に断る（`{{key}}` のまま実行させない）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-params-') } };
  withStubbedExec(() => {
    assert.throws(() => adhoc.submit(cfg, { request: '{{repo}} を直す' }), /入力してください: repo/);
    assert.throws(() => adhoc.submit(cfg, { request: '直す', parameters: { repo: 'x' } }),
      /未定義の入力パラメータです: repo/);
  });
});

test('埋めた値は要求文と plan の goal の両方へ入り、予約語は残る', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-params-fill-') } };
  withStubbedExec(() => {
    const result = adhoc.submit(cfg, {
      request: '{{repo}} の README を直す',
      preset: { name: 'F', nodes: [{ id: 'a', goal: '{{request}} を {{repo}} で行う' }] },
      parameters: { repo: 'sandbox' },
    });
    const rec = adhoc.readInbox(cfg.adhocFlow.busDir, result.runId);
    assert.strictEqual(rec.request, 'sandbox の README を直す');
    assert.strictEqual(rec.plan.nodes[0].goal, '{{request}} を sandbox で行う',
      '{{request}} の置換はエンジンの 1 か所に残す');
  });
});

// --- §1 編集付き再実行（fork） -----------------------------------------------

test('入力を編集した再実行は fork（旧 run は残り、変更点が証跡に残る）', () => {
  const busDir = tmpdir('reuse-fork-');
  const cfg = { adhocFlow: { busDir } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '最初の依頼' });
    const forked = adhoc.resubmit(cfg, first.runId, { request: '直した依頼' });
    const rec = adhoc.readInbox(busDir, forked.runId);
    assert.strictEqual(rec.request, '直した依頼');
    assert.deepStrictEqual(rec.edited_fields, ['request']);
    assert.strictEqual(rec.root_run_id, first.runId);
    assert.strictEqual(rec.previous_run_id, first.runId);
    assert.strictEqual(rec.inherit_from, undefined, 'fork は世代交代ではない（先行 run を墓標化しない）');
    assert.ok(fs.existsSync(path.join(busDir, 'inbox', `${first.runId}.json`)), '旧 run の記録は残る');
  });
});

test('同じ値を送り直しただけの再実行は「編集」として記録しない', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-fork-noop-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '依頼', granularity: 'fine' });
    const again = adhoc.resubmit(cfg, first.runId, { request: '依頼', granularity: 'fine' });
    const rec = adhoc.readInbox(cfg.adhocFlow.busDir, again.runId);
    assert.strictEqual(rec.edited_fields, undefined);
    assert.strictEqual(rec.granularity, 'fine');
  });
});

test('分け方を空へ戻した再実行はキーごと落とす（設定に従うへ戻せる）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-fork-clear-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '依頼', granularity: 'finest', splitPolicy: 'file' });
    const cleared = adhoc.resubmit(cfg, first.runId, { granularity: '' });
    const rec = adhoc.readInbox(cfg.adhocFlow.busDir, cleared.runId);
    assert.ok(!('granularity' in rec), 'granularity は書かない');
    assert.strictEqual(rec.split_policy, 'file', '触っていない項目は引き継ぐ');
    assert.deepStrictEqual(rec.edited_fields, ['granularity']);
  });
});

test('plan を差し替えた再実行は標準パターン指定を連れて行かない', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-fork-plan-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, {
      request: '依頼', selection: { type: 'pattern', id: 'map-reduce' },
    });
    assert.strictEqual(adhoc.readInbox(cfg.adhocFlow.busDir, first.runId).pattern, 'map-reduce');
    const forked = adhoc.resubmit(cfg, first.runId, {
      plan: { name: 'F', nodes: [{ id: 'a', goal: 'g', deps: [] }] },
    });
    const rec = adhoc.readInbox(cfg.adhocFlow.busDir, forked.runId);
    assert.strictEqual(rec.pattern, undefined, 'plan と pattern の同時指定は agent-flow が failed 終端させる');
    assert.strictEqual(rec.plan.name, 'F');
    assert.deepStrictEqual(rec.edited_fields, ['plan']);
  });
});

test('書込先を変えた再実行は共有リモートを確かめてから spec にする', () => {
  const busDir = tmpdir('reuse-fork-workspace-');
  const cfg = { adhocFlow: { busDir } };
  const shell = gitAwareShell({ '/repo-a': 'git@example.com:o/a.git', '/repo-b': 'git@example.com:o/b.git' });
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '依頼', cwd: '/repo-a' });
    const forked = adhoc.resubmit(cfg, first.runId, { cwd: '/repo-b' });
    const rec = adhoc.readInbox(busDir, forked.runId);
    assert.strictEqual(rec.workspace.url, 'git@example.com:o/b.git');
    assert.deepStrictEqual(rec.edited_fields, ['workspace']);
    // Git 管理外・共有リモート無しのフォルダは投函経路へ通さない（C1）
    assert.throws(() => adhoc.resubmit(cfg, first.runId, { cwd: '/tmp/anywhere' }), /Git 管理フォルダ/);
  }, shell);
});

test('編集なしの再実行はこれまでどおりの逐語複製', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-verbatim-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '依頼' });
    const again = adhoc.resubmit(cfg, first.runId);
    const rec = adhoc.readInbox(cfg.adhocFlow.busDir, again.runId);
    assert.strictEqual(rec.request, '依頼');
    assert.strictEqual(rec.edited_fields, undefined);
  });
});

// --- §2 / §3 蒸留と系譜 -------------------------------------------------------

test('保存形の複製元表記は session / run の 2 形だけを受ける', () => {
  assert.strictEqual(adhoc.workflowSourceFromRun('adhoc-20260831-1234'), 'run/adhoc-20260831-1234');
  assert.strictEqual(adhoc.workflowSourceFromSession('claude', 'sess-1'), 'session/claude/sess-1');
  assert.throws(() => adhoc.normalizeWorkflowSource('file:///home/me/transcript.jsonl'), /複製元の表記/);
  assert.strictEqual(adhoc.normalizeWorkflowSource(''), '', '手書きは省略できる');
});

test('複製元は保存形に残るが、実行される形（digest）は変えない', () => {
  const workflowDir = tmpdir('reuse-library-');
  const cfg = { adhocFlow: { workflowDir } };
  const base = {
    id: 'reuse-1', name: '流用フロー', purpose: 'implementation',
    nodes: [{ id: 'a', goal: 'やる', kind: 'work', tier: 'auto', deps: [] }],
  };
  const saved = adhoc.saveWorkflow(cfg, { ...base, source: 'run/adhoc-1' });
  assert.strictEqual(saved.source, 'run/adhoc-1');
  assert.strictEqual(adhoc.workflowDigest(saved), adhoc.workflowDigest(adhoc.normalizeWorkflow(base)));
});

test('run を種にした蒸留は inbox 記録から決定的に写す（LLM を通さない）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-distill-run-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, {
      request: '{{repo}} を直す',
      preset: { name: 'F', nodes: [{ id: 'a', goal: '{{repo}} の README を直す' }] },
      parameters: { repo: '{{repo}}' },
    });
    const workflow = reuse.distillRun(cfg, { runId: first.runId, kind: 'workflow' });
    assert.strictEqual(workflow.source, `run/${first.runId}`);
    assert.strictEqual(workflow.draft.workflow.nodes[0].id, 'a');
    assert.deepStrictEqual(workflow.draft.parameters, ['repo']);
    const request = reuse.distillRun(cfg, { runId: first.runId });
    assert.strictEqual(request.draft.kind, 'request');
    assert.strictEqual(request.draft.request, '{{repo}} を直す');
    assert.throws(() => reuse.distillRun(cfg, { runId: '../escape' }), /不正な run ID/);
  });
});

test('蒸留したフローは保存形として検証を通り、複製元が付く', () => {
  const cfg = { adhocFlow: { workflowDir: tmpdir('reuse-save-distilled-') } };
  const draft = reuse.normalizeWorkflowDraft({
    name: 'レビュー流用',
    nodes: [
      { id: 'Read Code', goal: '{{repo}} を読む', kind: 'work', deps: [] },
      { id: 'check', goal: '結果を検証する', kind: 'verify', deps: ['read-code', 'no-such-node'] },
    ],
  }, { id: 'session-1', tier: 'auto' });
  assert.deepStrictEqual(draft.workflow.nodes.map((node) => node.id), ['read-code', 'check']);
  assert.deepStrictEqual(draft.workflow.nodes[1].deps, ['read-code'], '実在しない依存は落とす');
  const saved = reuse.saveDistilled(cfg, { workflow: draft.workflow, source: 'session/claude/sess-1' });
  assert.strictEqual(saved.source, 'session/claude/sess-1');
  assert.strictEqual(saved._scope, 'user');
});

// --- §2 定型業務（statemachine-use）への蒸留 ---------------------------------

test('定型業務の蒸留は YAML を書かせず、作成モードへ渡す手順だけを求める', () => {
  const prompt = reuse.statemachineDraftPrompt('[User] 毎週これをやる', '');
  assert.match(prompt, /YAML は書かないでください/);
  assert.match(prompt, /statemachine-use スキルが作ります/);
  // スキルの分解原則（1 ステート 1 成果物・成功は機械が測る）を指示に載せる
  assert.match(prompt, /1 工程 1 成果物/);
  assert.match(prompt, /コマンドで測れるなら/);
  assert.match(prompt, /終了条件/);
  // 出力は下書きの 3 項目だけ（定義そのものは受け取らない）
  assert.match(prompt, /"name".*"machine".*"instruction"/);
});

test('定型業務の識別名は .statemachine/<id>/ に置ける形へ整える', () => {
  assert.strictEqual(reuse.sanitizeMachineId('Weekly Digest'), 'weekly-digest');
  assert.strictEqual(reuse.sanitizeMachineId('../escape'), 'escape');
  assert.strictEqual(reuse.sanitizeMachineId('..', 'fallback'), 'fallback');
  assert.strictEqual(reuse.sanitizeMachineId('', 'fallback'), 'fallback');
  // 受け側（cowork.generateStateMachine）が通す文字集合に収まること
  assert.match(reuse.sanitizeMachineId('週次まとめ v2'), /^[A-Za-z0-9_.-]+$/);
});

test('定型業務の下書きは手順が空なら受け取らない', () => {
  assert.throws(() => reuse.normalizeStatemachineDraft({ name: 'x', machine: 'y' }), /手順がありません/);
  const draft = reuse.normalizeStatemachineDraft({
    name: '週次まとめ', machine: 'Weekly Digest', instruction: '{{repo}} の変更をまとめる',
  });
  assert.strictEqual(draft.kind, 'statemachine');
  assert.strictEqual(draft.machine, 'weekly-digest');
  assert.deepStrictEqual(draft.parameters, ['repo'], '手順の `{{key}}` も入力パラメータとして拾う');
});

test('run からは定型業務を作らない（繰り返しはフォーク・一括投函が担う）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('reuse-run-sm-') } };
  withStubbedExec(() => {
    const first = adhoc.submit(cfg, { request: '依頼' });
    // 未知の kind は request へ倒れる（statemachine の枝を run 側に作らない）
    const draft = reuse.distillRun(cfg, { runId: first.runId, kind: 'statemachine' });
    assert.strictEqual(draft.draft.kind, 'request');
  });
});

test('蒸留のプロンプトは会話の再生ではなく一般化を求め、秘密の書き写しを禁じる', () => {
  const prompt = reuse.requestDraftPrompt('[User] 依頼\n\n[Assistant] 応答', '');
  assert.match(prompt, /一般化/);
  assert.match(prompt, /秘密情報・アクセストークン/);
  assert.match(prompt, /\{\{key\}\}/);
  // 本文は下書きの材料としてだけ渡す（プロンプトの外へは出さない）
  assert.match(prompt, /\[User\] 依頼/);
});

test('長い会話は前後だけを材料にし、中略を明示する', () => {
  const messages = Array.from({ length: 30 }, (_, index) => ({ role: 'User', text: `発言${index}` }));
  const body = reuse.transcriptDigest({ messages });
  assert.match(body, /発言0/);
  assert.match(body, /…（中略）…/);
  assert.match(body, /発言29/);
  assert.ok(!body.includes('発言15'));
  assert.strictEqual(reuse.transcriptDigest({ messages: [] }), '');
});

// --- §5 バッチ投函 -----------------------------------------------------------

test('バッチは件数上限を超えられない（すべての反復は有界）', () => {
  const rows = Array.from({ length: reuse.BATCH_MAX_ROWS + 1 }, () => ({ parameters: {} }));
  assert.throws(() => reuse.normalizeBatchRows(rows, []), /件までです/);
  assert.throws(() => reuse.normalizeBatchRows([], []), /1件以上/);
});

test('バッチの行は未入力・未定義キーを行番号つきで断る', () => {
  assert.throws(() => reuse.normalizeBatchRows([{ parameters: { repo: '' } }], ['repo']),
    /1 行目: 入力してください: repo/);
  assert.throws(() => reuse.normalizeBatchRows([{ parameters: { other: 'x' } }], ['repo']),
    /1 行目: 未定義の入力パラメータです: other/);
});

test('確認を通らない一括投函は 1 本も投函しない（C1）', () => {
  const busDir = tmpdir('reuse-batch-unconfirmed-');
  const cfg = { adhocFlow: { busDir } };
  withStubbedExec(() => {
    assert.throws(() => reuse.batchSubmit(cfg, {
      request: '依頼', rows: [{ parameters: {} }], parameterKeys: [],
    }), /確認が済んでいません/);
  });
  assert.ok(!fs.existsSync(path.join(busDir, 'inbox')), '投函口に何も書かない');
});

test('一括投函は行ごとに workspace を持ち、同じ batch_id で束ねる', () => {
  const busDir = tmpdir('reuse-batch-');
  const cfg = { adhocFlow: { busDir } };
  const shell = gitAwareShell({ '/repo-a': 'git@example.com:o/a.git', '/repo-b': 'git@example.com:o/b.git' });
  withStubbedExec(() => {
    const result = reuse.batchSubmit(cfg, {
      request: '{{target}} を直す',
      parameterKeys: ['target'],
      confirmed: true,
      rows: [
        { cwd: '/repo-a', parameters: { target: 'README' } },
        { cwd: '/repo-b', parameters: { target: 'CHANGELOG' } },
      ],
    });
    assert.strictEqual(result.failed, null);
    assert.strictEqual(result.submitted.length, 2);
    const records = result.submitted.map((row) => adhoc.readInbox(busDir, row.runId));
    assert.deepStrictEqual(records.map((rec) => rec.request), ['README を直す', 'CHANGELOG を直す']);
    assert.deepStrictEqual(records.map((rec) => rec.workspace.url),
      ['git@example.com:o/a.git', 'git@example.com:o/b.git']);
    assert.deepStrictEqual([...new Set(records.map((rec) => rec.batch_id))], [result.batchId],
      '1 回の一括投函は 1 つの batch_id で束ねる');
    // 行＝run は崩さない（行ごとに別の run。1 本の run が書込先の集合を持てるように
    // なった後も、バッチが 1 行を 2 run に割ることはない）
    assert.strictEqual(new Set(result.submitted.map((row) => row.runId)).size, 2);
  }, shell);
});

test('一括投函は 1 件目の失敗で止まり、そこまでの投函を報告する', () => {
  const busDir = tmpdir('reuse-batch-stop-');
  const cfg = { adhocFlow: { busDir } };
  const shell = gitAwareShell({ '/repo-a': 'git@example.com:o/a.git' });
  withStubbedExec(() => {
    const result = reuse.batchSubmit(cfg, {
      request: '直す',
      parameterKeys: [],
      confirmed: true,
      rows: [{ cwd: '/repo-a', parameters: {} }, { cwd: '/repo-missing', parameters: {} }],
    });
    assert.strictEqual(result.submitted.length, 1);
    assert.strictEqual(result.failed.index, 1);
    assert.match(result.failed.error, /Git 管理フォルダ/);
  }, shell);
});

test('概算予算は実測の有無を言い切る（数字の出どころを隠さない）', () => {
  const estimate = reuse.batchEstimate({ orchestration: { budgetDir: tmpdir('reuse-budget-') } }, 4);
  assert.strictEqual(estimate.count, 4);
  assert.strictEqual(estimate.measured, false);
  assert.strictEqual(estimate.estimatedTokens, reuse.FALLBACK_TOKENS_PER_RUN * 4);
  assert.strictEqual(estimate.maxRows, reuse.BATCH_MAX_ROWS);
});

// --- 画面（HTML 生成の契約） -------------------------------------------------

test('再実行は二択（同じ入力 / 入力を編集）で出す', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  workflowUi._state.runView = 'overview';
  workflowUi._state.fork = null;
  try {
    const html = workflowUi.runDetailHtml({
      run: { runId: 'run-1', status: 'done', nodes: { a: { id: 'a', state: 'done' } } },
      inbox: { request: '依頼', plan: { name: '自動' } },
      events: [],
    });
    assert.match(html, /id="wf-resubmit"[^>]*>[\s\S]*?同じ入力で再実行/);
    assert.match(html, /id="wf-resubmit-edit"/);
  } finally {
    global.esc = previousEsc;
  }
});

test('流用の来歴は inbox の系譜キーだけで組む（推測で束ねない）', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    assert.strictEqual(workflowUi.runLineageHtml({ inbox: {} }), '');
    const html = workflowUi.runLineageHtml({
      inbox: { previous_run_id: 'adhoc-0', edited_fields: ['request', 'workspace'], batch_id: 'batch-1' },
    });
    assert.match(html, /adhoc-0/);
    assert.match(html, /依頼内容、対象フォルダ/);
    assert.match(html, /batch-1/);
  } finally {
    global.esc = previousEsc;
  }
});

test('一括投函は確認前に投函ボタンを出さない', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    workflowUi._state.batch = {
      workflowId: 'w1', name: 'テンプレート', keys: ['repo'], rows: [{ cwd: '', parameters: {} }],
      preview: null, request: '', busy: '', error: '',
    };
    const before = workflowUi.batchDialogHtml();
    assert.match(before, /id="wf-batch-check"/);
    assert.ok(!before.includes('id="wf-batch-submit"'), '確認を飛ばす投函口を作らない');
    workflowUi._state.batch.preview = {
      rows: [{ index: 0, cwd: '/repo-a', unregistered: true, parameters: { repo: 'a' } }],
      estimate: { count: 1, maxRows: 20, perRunTokens: 100, estimatedTokens: 100, measured: true,
        sampleRuns: 3, tokenCap: 1000, remainingTokens: 900, exceeds: false },
    };
    const after = workflowUi.batchDialogHtml();
    assert.match(after, /id="wf-batch-submit"/);
    assert.match(after, /1 件を投函する/);
    assert.match(after, /担当を宣言していないリポジトリ/);
  } finally {
    workflowUi._state.batch = null;
    global.esc = previousEsc;
  }
});

test('入力ダイアログは全項目が埋まるまで実行できない', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    workflowUi._state.parameterDialog = { id: 'wft-1', title: 'T', keys: ['repo'], values: {}, error: '', busy: '' };
    const html = workflowUi.parameterDialogHtml();
    assert.match(html, /data-parameter="repo"/);
    assert.match(html, /id="wf-parameter-run" disabled/);
  } finally {
    workflowUi._state.parameterDialog = null;
    global.esc = previousEsc;
  }
});

test('セッションの行き先は 3 つ（依頼文 / ワークフロー / 定型業務）', () => {
  const previousState = global.state;
  global.state = { cowork: { roots: ['/repo-a', '/repo-b'] } };
  try {
    auditUi._sessions({ sessions: [{ native_id: 's1', agent_cli: 'claude', cwd: '/repo-a', turns: 4, updated_at: 0 }] });
    auditUi._seed({
      cli: 'claude', sessionId: 's1', cwd: '/repo-a', repo: '/repo-a', kind: 'statemachine',
      busy: '', error: '', source: 'session/claude/s1',
      draft: { kind: 'statemachine', name: '週次まとめ', machine: 'weekly-digest', instruction: '手順' },
    });
    const html = auditUi.seedDialogHtml();
    for (const kind of ['request', 'workflow', 'statemachine']) {
      assert.ok(html.includes(`value="${kind}"`), `行き先 ${kind} を選べる`);
    }
    // 作成先は登録済みフォルダからの選択だけ（任意パスへ .statemachine/ を作らせない）
    assert.match(html, /id="audit-seed-repo"/);
    assert.match(html, /<option value="\/repo-a" selected>/);
    assert.match(html, /id="audit-seed-machine"/);
    assert.match(html, /id="audit-seed-instruction"/);
    assert.match(html, /定型業務を作成/);
    // YAML はこの画面では書かない（書式の正典は statemachine-use スキル）
    assert.match(html, /statemachine-use スキル/);
    assert.ok(!html.includes('id="audit-seed-confirm" disabled'), '作成先があれば確定できる');
  } finally {
    auditUi._seed(null);
    auditUi._sessions(null);
    global.state = previousState;
  }
});

test('登録済みフォルダが無い端末では定型業務を作らせない', () => {
  const previousState = global.state;
  global.state = { cowork: { roots: [] } };
  try {
    auditUi._seed({
      cli: 'claude', sessionId: 's1', kind: 'statemachine', busy: '', error: '',
      source: 'session/claude/s1',
      draft: { kind: 'statemachine', name: '', machine: '', instruction: '手順' },
    });
    const html = auditUi.seedDialogHtml();
    assert.match(html, /id="audit-seed-confirm" disabled/);
    assert.match(html, /フォルダを登録してください/);
  } finally {
    auditUi._seed(null);
    global.state = previousState;
  }
});

test('保存形の複製元は一覧でも辿れる', () => {
  assert.strictEqual(workflowUi.workflowSourceLabel({ source: 'run/adhoc-1' }), 'run adhoc-1 から');
  assert.strictEqual(workflowUi.workflowSourceLabel({ source: 'session/claude/x' }), 'claude のセッションから');
  assert.strictEqual(workflowUi.workflowSourceLabel({}), '');
});

console.log(`\n${passed} reuse-rerun tests passed`);
