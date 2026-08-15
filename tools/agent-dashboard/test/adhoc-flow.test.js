'use strict';

// coherence: code=tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js, doc=docs/plans/2026-08-10-agent-tools-human-extract-retrieve-implementation-plan.md

// adhoc-flow（S21/S22: プロジェクト非依存の flow 投入・フロービルダー・昇格）の単体テスト。
// Electron は起動しない。追加依存なしで `node test/adhoc-flow.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
require('./flow-interaction.test');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const design = require('../src/features/adhoc-flow/main/design-session');
const taskQueue = require('../src/features/adhoc-flow/main/task-queue');
const exec = require('../src/features/routines/main/exec');
const tuning = require('../src/features/orchestration/main/tuning');
const profiles = require('../src/features/orchestration/main/profiles');
const actions = require('../src/features/agent-project/main/actions');
const projectEngine = require('../src/features/agent-project/main/engine');
const workflowUi = require('../src/renderer/features/adhoc-flow');

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

test('実行時方針のおすすめは agent-control / agent-flow の自動決定へ委ねる', () => {
  assert.strictEqual(workflowUi.executionOverridesForMode('recommended', {}), null);
});

test('ワークフロータスクは作成時に実行せず、実行待ちとして保存する', () => {
  const dir = tmpdir('adhoc-task-queue-');
  try {
    const config = { adhocFlow: { taskQueueDir: dir } };
    const created = taskQueue.create(config, {
      title: 'READMEを更新', request: '## 目的\nREADMEを更新', cwd: '/repo',
      selection: { type: 'auto' }, sourceMode: 'new',
    });
    assert.match(created.id, /^wft-/);
    assert.strictEqual(taskQueue.list(config).length, 1);
    assert.strictEqual(taskQueue.get(config, created.id).request, '## 目的\nREADMEを更新');
    assert.ok(!created.runId, '作成時にはrunを開始しない');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('実行待ちタスクは明示実行に成功した後だけ一覧から除く', () => {
  const dir = tmpdir('adhoc-task-queue-');
  try {
    const config = { adhocFlow: { taskQueueDir: dir } };
    const created = taskQueue.create(config, { title: '実行する', request: '本文' });
    assert.throws(() => taskQueue.execute(config, created.id, () => { throw new Error('起動失敗'); }), /起動失敗/);
    assert.ok(taskQueue.get(config, created.id), '失敗時は実行待ちを保持');
    const result = taskQueue.execute(config, created.id, (payload) => ({ runId: 'run-1', payload }));
    assert.strictEqual(result.runId, 'run-1');
    assert.strictEqual(result.payload.title, '実行する', '実行詳細へタスク名を引き継ぐ');
    assert.strictEqual(taskQueue.get(config, created.id), null);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('ワークフロー作成は要望の後に三つの準備経路を選び、その後で材料を指定する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    workflowUi._state.taskWizard = {
      step: 2, goal: 'CSV対応を改善する', route: 'agent-design',
      recommendation: { route: 'agent-design', reasons: ['完了条件が不足'] }, materials: [], error: '', busy: '',
    };
    const html = workflowUi.workflowTaskDialogHtml({ cwdHistory: [] });
    assert.ok(html.includes('やりたいこと') && html.includes('進め方') && html.includes('材料') && html.includes('確認')
      && html.includes('エージェントと設計する') && html.includes('外部の設計結果を使う')
      && html.includes('そのまま実装する') && html.indexOf('進め方') < html.indexOf('材料'));
  } finally { global.esc = previousEsc; }
});

test('実行時方針のコスト優先は役割・機能ごとの最低許可tierを選ぶ', () => {
  const result = workflowUi.executionOverridesForMode('cost', { kindTiers: {
    roles: { planner: { tiers: ['medium', 'large'] }, worker: { tiers: ['small', 'medium', 'large'] } },
    kinds: { classify: { tiers: ['basic', 'small', 'medium', 'large'] }, synthesize: { tiers: ['medium', 'large'] } },
  } });
  assert.deepStrictEqual(result, {
    version: 1,
    mode: 'cost',
    roles: { planner: { tier: 'medium' }, worker: { tier: 'small' } },
    kinds: { classify: { tier: 'basic' }, synthesize: { tier: 'medium' } },
  });
});

test('実行時方針の節約と品質優先は許可範囲内でsmallとlargeへ寄せる', () => {
  const overview = { kindTiers: {
    roles: { planner: { tiers: ['medium', 'large'] }, worker: { tiers: ['small', 'medium', 'large'] } },
    kinds: { classify: { tiers: ['basic', 'small', 'medium', 'large'] }, synthesize: { tiers: ['medium', 'large'] } },
  } };
  assert.deepStrictEqual(workflowUi.executionOverridesForMode('saving', overview), {
    version: 1, mode: 'saving',
    roles: { planner: { tier: 'medium' }, worker: { tier: 'small' } },
    kinds: { classify: { tier: 'small' }, synthesize: { tier: 'medium' } },
  });
  assert.deepStrictEqual(workflowUi.executionOverridesForMode('quality', overview), {
    version: 1, mode: 'quality',
    roles: { planner: { tier: 'large' }, worker: { tier: 'large' } },
    kinds: { classify: { tier: 'large' }, synthesize: { tier: 'large' } },
  });
});

test('実行タブは選択した標準フローのカード説明と工程構成を表示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.selectedFlowSummaryHtml({ patterns: [{
      id: 'build-and-verify', label: '実装して検証', description: '成果を作り、条件を満たすか検証します。',
      template: { nodes: [
        { id: 'build', kind: 'work', deps: [] },
        { id: 'verify', kind: 'verify', deps: ['build'] },
      ] },
    }] }, 'pattern:build-and-verify');
    assert.match(html, /標準フロー/);
    assert.match(html, /実装して検証/);
    assert.match(html, /成果を作り、条件を満たすか検証します。/);
    assert.match(html, /作業/);
    assert.match(html, /検証/);
    assert.doesNotMatch(html, /編集を始める/);
    assert.ok(html.includes('wf-selected-flow-heading')
      && html.indexOf('wf-selected-flow-heading') < html.indexOf('wf-mini-flow'),
      'バッジとタイトルをカード左上へ配置します');
    assert.ok(html.indexOf('wf-mini-flow') < html.indexOf('wf-selected-flow-description'),
      'ミニ工程図を説明文より左へ配置します');
  } finally {
    global.esc = previousEsc;
  }
});

test('実行タブは選択したカスタムフローのカード説明と工程数を表示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.selectedFlowSummaryHtml({ workflows: [{
      id: 'release-flow', name: 'リリース準備', description: '実装、検証、リリース判定を順に行います。',
      nodes: [{ id: 'build' }, { id: 'verify' }, { id: 'judge' }],
    }] }, 'custom:release-flow');
    assert.match(html, /カスタムフロー/);
    assert.match(html, /リリース準備/);
    assert.match(html, /実装、検証、リリース判定を順に行います。/);
    assert.match(html, /3工程/);
  } finally {
    global.esc = previousEsc;
  }
});

test('実行タブは自動フローで実行時に工程を計画することを説明する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.selectedFlowSummaryHtml({}, 'auto');
    assert.match(html, /自動/);
    assert.match(html, /依頼に合わせて工程を計画/);
  } finally {
    global.esc = previousEsc;
  }
});

test('カスタムフローをユーザー共通ファイルとして保存・読込できる', () => {
  const cfg = { adhocFlow: { workflowDir: tmpdir('workflow-files-') } };
  const saved = adhoc.saveWorkflow(cfg, {
    name: '実装と検証',
    nodes: [
      { id: 'build', goal: '実装: {{request}}', tier: 'large', x: 40, y: 60 },
      { id: 'verify', goal: '検証', kind: 'verify', tier: 'small', deps: ['build'], x: 320, y: 60 },
    ],
  });
  assert.ok(saved.id.startsWith('workflow-'));
  // 一覧には同梱フローも並ぶので、自分用スコープだけを数える
  assert.strictEqual(adhoc.listWorkflows(cfg).filter((item) => item._scope === 'user').length, 1);
  assert.deepStrictEqual(adhoc.loadWorkflow(cfg, saved.id), saved);
  assert.strictEqual(saved.nodes[1].tier, 'small');
  assert.strictEqual(saved.nodes[1].x, 320);
  assert.strictEqual(saved.version, 2);
  assert.deepStrictEqual(saved.entry, ['build']);
  assert.deepStrictEqual(saved.exit, ['verify']);
});

test('既存ワークフローは実装用のライブラリ項目として正規化する', () => {
  const workflow = adhoc.normalizeWorkflow({
    id: 'legacy-flow', name: '既存フロー',
    nodes: [{ id: 'work', goal: '実装する', kind: 'work', tier: 'small', deps: [] }],
  });
  assert.strictEqual(workflow.purpose, 'implementation');
  assert.strictEqual(workflow.libraryVisibility, 'library');
});

test('Git リポジトリ内のカスタムフローは共有版を優先して読むが dashboard から変更しない', () => {
  const repo = tmpdir('workflow-repository-');
  fs.mkdirSync(path.join(repo, '.git'));
  const subdir = path.join(repo, 'src', 'nested');
  fs.mkdirSync(subdir, { recursive: true });
  const cfg = { adhocFlow: { workflowDir: tmpdir('workflow-user-') } };
  const base = { id: 'shared-flow', name: 'ユーザー版', nodes: [{ id: 'work', goal: 'user', tier: 'auto' }] };
  adhoc.saveWorkflow(cfg, base);
  const sharedDir = path.join(repo, '.agents', 'workflows');
  fs.mkdirSync(sharedDir, { recursive: true });
  fs.writeFileSync(path.join(sharedDir, 'shared-flow.json'), `${JSON.stringify({
    ...base, version: 2, name: '共有版', entry: ['work'], exit: ['work'],
    nodes: [{ id: 'work', goal: 'repo', tier: 'auto' }],
  })}\n`);
  const originalRoots = projectEngine.projectRoots;
  projectEngine.projectRoots = () => [repo];
  const listed = adhoc.listWorkflows(cfg, { cwd: subdir });
  assert.strictEqual(listed.filter((item) => item.id === 'shared-flow').length, 1);
  const shared = listed.find((item) => item.id === 'shared-flow');
  assert.strictEqual(shared.name, '共有版');
  assert.strictEqual(shared._scope, 'repository');
  assert.strictEqual(shared._repository, repo);
  assert.strictEqual(adhoc.snapshotSelection(cfg, { type: 'custom', id: 'shared-flow' }, { cwd: subdir }).nodes[0].goal, 'repo');
  assert.throws(() => adhoc.saveWorkflow(cfg, { ...shared, name: '変更' }), /読み取り専用/);
  assert.throws(() => adhoc.deleteWorkflow(cfg, 'shared-flow', { scope: 'repository', cwd: subdir }), /読み取り専用/);
  assert.strictEqual(JSON.parse(fs.readFileSync(path.join(sharedDir, 'shared-flow.json'), 'utf8')).name, '共有版');
  projectEngine.projectRoots = originalRoots;
});

test('未登録フォルダのフローと作業ルールは探索しない', () => {
  const repo = tmpdir('workflow-unregistered-');
  fs.mkdirSync(path.join(repo, '.git'));
  fs.mkdirSync(path.join(repo, '.agents', 'workflows'), { recursive: true });
  fs.mkdirSync(path.join(repo, '.agents', 'methods'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'workflows', 'hidden.json'), JSON.stringify({
    version: 2, id: 'hidden', name: '未登録', entry: ['work'], exit: ['work'],
    nodes: [{ id: 'work', goal: 'hidden', tier: 'auto' }],
  }));
  fs.writeFileSync(path.join(repo, '.agents', 'methods', 'hidden.json'), JSON.stringify({
    id: 'hidden', description: '未登録', fragments: [{ role: 'worker', text: 'hidden' }], when: {},
  }));
  const originalRoots = projectEngine.projectRoots;
  projectEngine.projectRoots = () => [];
  try {
    assert.ok(!adhoc.listWorkflows({}, { cwd: repo }).some((item) => item.id === 'hidden'));
    assert.ok(!adhoc.availableMethods({}, { cwd: repo }).some((item) => item._from === 'repository'));
  } finally { projectEngine.projectRoots = originalRoots; }
});

test('ノードの追加ルールも Git リポジトリから読み、選択時に本文と source hash を複製する', () => {
  const repo = tmpdir('method-repository-');
  fs.mkdirSync(path.join(repo, '.git'));
  fs.mkdirSync(path.join(repo, 'src'));
  const methodsDir = path.join(repo, '.agents', 'methods');
  fs.mkdirSync(methodsDir, { recursive: true });
  const method = {
    id: 'repo-test-first', description: 'リポジトリのテスト規律', enabled: true,
    fragments: [{ role: 'worker', text: 'このリポジトリでは失敗するテストを先に追加する。' }],
    when: { engines: ['agent-flow'], purposes: ['work'] },
  };
  fs.writeFileSync(path.join(methodsDir, 'repo-test-first.json'), `${JSON.stringify(method)}\n`);
  const cfg = { orchestration: { methodsDir: tmpdir('method-user-') } };
  const originalRoots = projectEngine.projectRoots;
  projectEngine.projectRoots = () => [repo];
  const found = adhoc.availableMethods(cfg, { cwd: path.join(repo, 'src') })
    .find((item) => item.id === method.id);
  assert.ok(found);
  assert.strictEqual(found._from, 'repository');
  assert.strictEqual(found.fragments[0].text, method.fragments[0].text);
  assert.strictEqual(found.source,
    `repository:.agents/methods/repo-test-first.json@${tuning.sourceHash(method)}`);
  const choice = workflowUi.nodeMethodChoices([found], { kind: 'work', tier: 'auto' })[0];
  assert.strictEqual(choice.text, method.fragments[0].text);
  assert.strictEqual(choice.source, found.source);
  projectEngine.projectRoots = originalRoots;
});

test('version 2 は開始から終了までの実行経路だけを保存する', () => {
  const valid = adhoc.normalizeWorkflow({
    version: 2, name: '経路', entry: ['build'], exit: ['verify'],
    nodes: [
      { id: 'build', label: '実装', goal: '実装する', tier: 'large' },
      { id: 'verify', label: '検証', goal: '検証する', kind: 'verify', tier: 'small', deps: ['build'] },
    ],
  });
  assert.deepStrictEqual(valid.entry, ['build']);
  assert.deepStrictEqual(valid.exit, ['verify']);
  assert.strictEqual(valid.nodes[0].label, '実装');
  assert.throws(() => adhoc.normalizeWorkflow({
    version: 2, name: '開始なし', entry: [], exit: ['a'],
    nodes: [{ id: 'a', goal: 'x', tier: 'large' }],
  }), /開始/);
  assert.throws(() => adhoc.normalizeWorkflow({
    version: 2, name: '終了なし', entry: ['a'], exit: [],
    nodes: [{ id: 'a', goal: 'x', tier: 'large' }],
  }), /終了/);
  assert.throws(() => adhoc.normalizeWorkflow({
    version: 2, name: 'split後段', entry: ['split'], exit: ['next'],
    nodes: [
      { id: 'split', goal: '分割', kind: 'split', tier: 'large' },
      { id: 'next', goal: '続行', tier: 'large', deps: ['split'] },
    ],
  }), /split/);
});

test('カスタムフローの保存境界は壊れたグラフを拒否し、削除は復元可能な場所へ移す', () => {
  const cfg = { adhocFlow: { workflowDir: tmpdir('workflow-validation-') } };
  assert.throws(() => adhoc.normalizeWorkflow(null), /不正/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: '' }), /フロー名/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: 'x', nodes: [] }), /1つ以上/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: 'x', nodes: [
    { id: 'a', goal: 'g', tier: 'large' }, { id: 'a', goal: 'g', tier: 'large' },
  ] }), /重複/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: 'x', nodes: [
    { id: 'a', goal: 'g', tier: 'large', deps: ['missing'] },
  ] }), /接続先/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: 'x', nodes: [
    { id: 'a', goal: 'g', tier: 'large', deps: ['a'] },
  ] }), /自分自身/);
  assert.throws(() => adhoc.normalizeWorkflow({ name: 'x', nodes: [
    { id: 'a', goal: 'g', tier: 'large', deps: ['b'] },
    { id: 'b', goal: 'g', tier: 'large', deps: ['a'] },
  ] }), /循環/);
  const saved = adhoc.saveWorkflow(cfg, { name: 'x', nodes: [{ id: 'a', goal: 'g', tier: 'large' }] });
  const updated = adhoc.saveWorkflow(cfg, { ...saved, name: 'y' });
  assert.strictEqual(updated.createdAt, saved.createdAt);
  assert.strictEqual(adhoc.deleteWorkflow(cfg, saved.id), true);
  assert.strictEqual(adhoc.deleteWorkflow(cfg, saved.id), false);
  assert.strictEqual(adhoc.loadWorkflow(cfg, saved.id), null);
  assert.ok(fs.existsSync(path.join(cfg.adhocFlow.workflowDir, '.trash')));
});

test('カスタムフローの tier を実行候補へ固定して plan を作る', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = (_cfg, tier) => (
    tier === 'large' ? { agent_cli: 'codex', model: 'gpt-5' } : { agent_cli: 'ollama', model: 'qwen3' }
  );
  try {
    const workflow = adhoc.normalizeWorkflow({
      name: '実装と抽出',
      nodes: [
        { id: 'build', goal: '実装', tier: 'large' },
        { id: 'pick', goal: '項目を抽出', kind: 'extract', tier: 'basic', deps: ['build'] },
      ],
    });
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.strictEqual(plan.nodes[0].tier, 'large');
    assert.strictEqual(plan.nodes[1].tier, 'basic');
    assert.deepStrictEqual(plan.nodes[0].agent, { agent_cli: 'codex', model: 'gpt-5' });
    assert.deepStrictEqual(plan.nodes[1].agent, { agent_cli: 'ollama', model: 'qwen3' });
  } finally {
    profiles.resolveTier = original;
  }
});

test('機能・オプションの実行可能レベル外の固定 tier は plan 生成で弾く', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'ollama', model: 'qwen3' });
  try {
    // work（成果物を作る機能）は単純作業（basic）へ任せない
    assert.throws(() => adhoc.planFromWorkflow({}, adhoc.normalizeWorkflow({
      name: '不適格', nodes: [{ id: 'build', goal: '実装', tier: 'basic' }],
    })), /「build」（work）は実行レベル「単純作業」に任せられません/);
    // verify 単体は軽量で実行できるが、retry オプションで下限が標準へ上がる
    assert.throws(() => adhoc.planFromWorkflow({}, adhoc.normalizeWorkflow({
      name: '不適格retry',
      nodes: [
        { id: 'work', goal: '作業', tier: 'small' },
        { id: 'check', goal: '検証', kind: 'verify', tier: 'small', deps: ['work'],
          continuation: 'retry' },
      ],
    })), /「check」（verify）は実行レベル「軽量」に任せられません/);
    // 同じ small の verify も、retry を外せば適格
    const plan = adhoc.planFromWorkflow({}, adhoc.normalizeWorkflow({
      name: '適格',
      nodes: [
        { id: 'work', goal: '作業', tier: 'small' },
        { id: 'check', goal: '検証', kind: 'verify', tier: 'small', deps: ['work'] },
      ],
    }));
    assert.strictEqual(plan.nodes[1].tier, 'small');
  } finally {
    profiles.resolveTier = original;
  }
});

test('複数レベルで実行可能な自動 tier は、実行方針の戦略に応じて実行可能範囲内で決まる', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = (_cfg, tier) => ({ agent_cli: 'claude', model: `m-${tier}` });
  const controlDir = tmpdir('flow-tier-strategy-');
  const cfg = { orchestration: { controlDir } };
  const writeStrategy = (mode, steps, flowTier) => {
    fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
      version: 1,
      enabled: true,
      tiers: {},
      policy: { apply_to: ['flow'], steps, no_cap_tier: steps[0].tier },
      execution_policy: { mode, custom: {} },
    }));
    fs.writeFileSync(path.join(controlDir, 'control.json'), JSON.stringify({
      version: 1, revision: 1, workloads: { flow: { tier: flowTier } },
    }));
  };
  try {
    // 節約（常に small）: judge は標準以上なので、最も低い適格レベル medium へ固定する
    writeStrategy('saving', [{ min_remaining_ratio: 0, tier: 'small' }], 'small');
    const saving = adhoc.planFromWorkflow(cfg, adhoc.normalizeWorkflow({
      name: '判定', nodes: [
        { id: 'gen', goal: '候補を作る', kind: 'generate', tier: '自動' },
        { id: 'pick', goal: '候補を選ぶ', kind: 'judge', tier: '自動', deps: ['gen'] },
      ],
    }));
    // generate は small..large すべて適格 → 実行時の方針追従（継承）のまま
    assert.strictEqual(saving.nodes[0].tier, undefined);
    assert.strictEqual(saving.nodes[0].agent, undefined);
    // judge は方針が不適格な段（small）を選びうる → 実行可能範囲へ丸めて固定
    assert.strictEqual(saving.nodes[1].tier, 'medium');
    assert.deepStrictEqual(saving.nodes[1].agent, { agent_cli: 'claude', model: 'm-medium' });
    assert.match(String(saving.nodes[1].selection_reason || ''), /strategy=saving/);

    // 品質優先（large→medium→small）: 今の段が large なら judge も large のまま固定
    writeStrategy('quality', [
      { min_remaining_ratio: 0.2, tier: 'large' },
      { min_remaining_ratio: 0.05, tier: 'medium' },
      { min_remaining_ratio: 0, tier: 'small' },
    ], 'large');
    const quality = adhoc.planFromWorkflow(cfg, adhoc.normalizeWorkflow({
      name: '判定', nodes: [{ id: 'pick', goal: '候補を選ぶ', kind: 'judge', tier: '自動' }],
    }));
    assert.strictEqual(quality.nodes[0].tier, 'large');
  } finally {
    profiles.resolveTier = original;
  }
});

test('カスタムフローの自動 tier は agent-control の実行方針を継承する', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = () => { throw new Error('auto は固定候補を解決しない'); };
  try {
    const workflow = adhoc.normalizeWorkflow({
      name: '自動選択', nodes: [{ id: 'work', goal: '実装', tier: '自動' }],
    });
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.strictEqual(workflow.nodes[0].tier, 'auto');
    assert.strictEqual(plan.nodes[0].tier, undefined);
    assert.strictEqual(plan.nodes[0].agent, undefined);
  } finally {
    profiles.resolveTier = original;
  }
});

test('human は agent を持たず対話契約を保ち、extract と retrieve は通常の worker として plan 化する', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'ollama', model: 'qwen3' });
  try {
    const workflow = adhoc.normalizeWorkflow({
      name: '人の確認と根拠取得',
      nodes: [
        { id: 'ask', goal: '公開可否を確認する', kind: 'human', deps: [], interaction: {
          mode: 'choice', prompt: '公開しますか', audience: ['reviewer'],
          options: ['公開', '保留'], default_option: '保留', timeout_seconds: 3600,
        } },
        { id: 'get', goal: '資料を取得する', kind: 'retrieve', tier: 'medium', deps: ['ask'] },
        { id: 'pick', goal: '項目を抽出する', kind: 'extract', tier: 'medium', deps: ['get'] },
      ],
    });
    assert.strictEqual(workflow.nodes[0].tier, undefined);
    assert.strictEqual(workflow.nodes[0].interaction.default_option, '保留');
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.strictEqual(plan.nodes[0].agent, undefined);
    assert.strictEqual(plan.nodes[0].interaction.prompt, '公開しますか');
    assert.strictEqual(plan.nodes[1].agent.agent_cli, 'ollama');
    assert.strictEqual(plan.nodes[2].kind, 'extract');
    assert.strictEqual(profiles.resolveTier === original, false);
  } finally {
    profiles.resolveTier = original;
  }
});

test('編集可能な種別は engine と一致し、system role を worker kind に偽装しない', () => {
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, '../../../schemas/agent-node-data.schema.json'), 'utf8'));
  assert.deepStrictEqual(workflowUi.KINDS, schema.properties.kind.enum);
  assert.deepStrictEqual(adhoc.NODE_KINDS, schema.properties.kind.enum);
  assert.strictEqual(workflowUi.roleLabelForKind('classify'), '作業');
  assert.strictEqual(workflowUi.roleLabelForKind('judge'), '作業');
  assert.strictEqual(workflowUi.roleLabelForKind('human'), '人間');
  assert.strictEqual(workflowUi.roleLabelForKind('verify'), '検証');
});

test('ノードの継続動作を保存し、agent-flow の継続評価を有効にする', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'gpt-5' });
  try {
    const workflow = adhoc.normalizeWorkflow({
      name: '完了まで反復',
      nodes: [
        { id: 'work', goal: '作業', kind: 'work', tier: 'medium' },
        { id: 'verify', goal: '完了条件を確認', kind: 'verify', tier: 'medium',
          deps: ['work'], continuation: 'retry' },
      ],
    });
    assert.strictEqual(workflow.nodes[1].continuation, 'retry');
    assert.strictEqual(adhoc.planFromWorkflow({}, workflow).evaluate, true);
    assert.strictEqual(adhoc.planFromWorkflow({}, {
      ...workflow, nodes: workflow.nodes.map((node) => ({ ...node, continuation: undefined })),
    }).evaluate, undefined, '通常の固定フローには隠れた評価を追加しない');
  } finally {
    profiles.resolveTier = original;
  }
});

test('旧全体ルールは捨て、工程の実行手法だけを指示へ反映する', () => {
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'gpt-5' });
  try {
    const workflow = adhoc.normalizeWorkflow({
      version: 2, name: '手法つき', entry: ['build'], exit: ['build'], methods: ['plan-first'],
      nodes: [{
        id: 'build', label: 'テスト先行', goal: '{{request}}', tier: 'large',
        method: {
          id: 'test-first', description: '失敗するテストを先に置く', role: 'worker',
          text: '失敗する最小テストを先に追加してください。', source: 'methods/test-first@abc',
        },
      }],
    });
    assert.strictEqual(workflow.methods, undefined);
    assert.strictEqual(workflow.nodes[0].method.id, 'test-first');
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.strictEqual(plan.methods, undefined);
    assert.match(plan.nodes[0].goal, /失敗する最小テスト/);
    assert.strictEqual(plan.nodes[0].method, undefined, '実行エンジンへは通常の goal として渡す');
  } finally {
    profiles.resolveTier = original;
  }
});

test('工程向け実行手法を、役割の合う工程だけのオプションにする', () => {
  const methods = [{
    id: 'adversarial-verify', description: '反例を探してから検証判定する', origin: 'local:test',
    fragments: [{ role: 'verify', text: '具体的な反例を探してください。' }],
    when: { tiers: ['small'], purposes: ['verify'] },
  }, {
    id: 'test-first', description: 'テストを先に置く',
    fragments: [{ role: 'worker', text: '失敗するテストを先に追加してください。' }],
  }, {
    id: 'failure-modes-first', description: '失敗を先に考える',
    fragments: [{ role: 'planner', text: '計画する' }, { role: 'worker', text: '失敗を列挙する' }],
  }, {
    id: 'persist-until-done', description: '未解決のまま完了を名乗らない',
    fragments: [{ role: 'worker', text: '解決まで続ける' }, { role: 'session', text: '未解決なら明示する' }],
  }, {
    id: 'plan-options', description: '計画を具体化する',
    fragments: [{ role: 'planner', text: '手順を計画する' }],
  }, {
    id: 'checklist-acceptance', description: '完了条件を照合する',
    fragments: [{ role: 'evaluator', text: '完了条件を確認する' }],
  }];
  const choices = workflowUi.nodeMethodChoices(methods, { kind: 'verify', tier: 'small' });
  assert.deepStrictEqual(choices.map((choice) => choice.id), ['adversarial-verify']);
  assert.strictEqual(choices[0].role, 'verify');
  assert.strictEqual(choices[0].text, '具体的な反例を探してください。');
  assert.strictEqual(choices[0].source, 'local:test');
  assert.deepStrictEqual(workflowUi.nodeMethodChoices(methods, { kind: 'verify', tier: 'large' }), []);
  assert.deepStrictEqual(workflowUi.nodeMethodChoices(methods, { kind: 'work', tier: 'small' })
    .map((choice) => choice.id), ['test-first', 'failure-modes-first', 'persist-until-done']);
  assert.deepStrictEqual(workflowUi.nodeMethodChoices(methods, { kind: 'classify', tier: 'small' })
    .map((choice) => choice.id), ['test-first', 'failure-modes-first', 'persist-until-done']);
  assert.deepStrictEqual(workflowUi.nodeMethodChoices(methods, { kind: 'judge', tier: 'small' })
    .map((choice) => choice.id), ['test-first', 'failure-modes-first', 'persist-until-done']);
});

test('複数ロールの実行手法を、接続済みの工程セットへ変換する', () => {
  const pattern = workflowUi.methodWorkflowPattern({
    id: 'plan-build-verify', description: '計画して作業し検証する', origin: 'local:test',
    fragments: [
      { role: 'worker', text: '作業する' },
      { role: 'verify', text: '別の観点で検証する' },
      { role: 'evaluator', text: '完了を判定する' },
    ],
  });
  assert.strictEqual(pattern.type, 'method');
  assert.deepStrictEqual(pattern.template.nodes.map((node) => node.kind), ['work', 'verify']);
  assert.deepStrictEqual(pattern.template.nodes.map((node) => node.deps), [[], ['plan-build-verify-1']]);
  assert.deepStrictEqual(pattern.template.nodes.map((node) => node.method.role), ['worker', 'verify']);
  assert.strictEqual(workflowUi.methodWorkflowPattern({
    id: 'test-first', fragments: [{ role: 'worker', text: 'テストする' }],
  }), null, '単一ロールは工程オプションのままにする');
  assert.strictEqual(workflowUi.methodWorkflowPattern({
    id: 'persist-until-done',
    fragments: [{ role: 'worker', text: '続ける' }, { role: 'session', text: '未解決を明示する' }],
  }), null, 'session 指示は新しい工程を作らず作業工程のオプションにする');
  assert.strictEqual(workflowUi.methodWorkflowPattern({
    id: 'no-self-approval',
    fragments: [{ role: 'verify', text: '検証する' }, { role: 'evaluator', text: '判定する' }],
  }), null, '分離済みセッションの自己承認回避は工程セットに重ねない');
});

test('system role だけで構成された手法を編集ノードへ偽装しない', () => {
  const pattern = workflowUi.methodWorkflowPattern({
    id: 'derive-twice', description: '別解で再導出して一致を確認する',
    fragments: [
      { role: 'planner', text: '2つの解法を計画する' },
      { role: 'verify', text: '別解で再導出する' },
    ],
  });
  assert.strictEqual(pattern, null, '複数案は既存の並列統合パターンで表し、planner を分類ノードにしない');
});

test('分類して実行と同型の失敗モード手法は別カードにせず工程オプションへ統合する', () => {
  const method = {
    id: 'failure-modes-first', description: '失敗モードと回復手段を先に洗い出す',
    fragments: [
      { role: 'planner', text: '失敗を計画する' },
      { role: 'worker', text: '回復手段を実装する' },
    ],
  };
  assert.strictEqual(workflowUi.methodWorkflowPattern(method), null);
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({ workflows: [], methods: [method], patterns: [{
      id: 'classify-and-act', label: '分類して実行', description: '分類後に実行する',
      template: { nodes: [{ id: 'classify', kind: 'classify', deps: [] }] },
    }] });
    assert.match(html, /data-pattern-id="classify-and-act"/);
    assert.doesNotMatch(html, /data-method-pattern-id="failure-modes-first"/);
  } finally {
    global.esc = previousEsc;
  }
});

test('標準パターンを開始・終了つきの編集可能フローへ複製する', () => {
  const workflow = workflowUi.workflowFromPattern({
    id: 'adversarial-verification', label: '生成して検証', description: '生成後に検証する',
    template: { nodes: [
      { id: 'gen1', goal: '{{request}}', kind: 'generate', deps: [] },
      { id: 'verify1', goal: '成果を批判的に検証', kind: 'verify', deps: ['gen1'] },
    ] },
  }, 'small');
  assert.strictEqual(workflow.id, '');
  assert.match(workflow.name, /生成して検証/);
  assert.deepStrictEqual(workflow.entry, ['gen1']);
  assert.deepStrictEqual(workflow.exit, ['verify1']);
  assert.strictEqual(workflow.nodes[0].tier, 'small');
  assert.deepStrictEqual(workflow.nodes.map((node) => node.label), ['生成', '検証'],
    'ロールとは別にワークフロー上のノード名を保持する');
  assert.strictEqual(workflow.nodes[0].goal, workflowUi.defaultGoal('generate'),
    '置換記号だけの目的は、工程の役割が分かる既定文へ補う');
  assert.strictEqual(workflow.nodes[1].goal, '成果を批判的に検証',
    '雛形に具体的な目的があれば変更しない');
  assert.ok(workflow.nodes[1].x > workflow.nodes[0].x, '依存方向へ自動配置する');
});

test('動的な標準パターンは継続動作を対象ノードへ保持する', () => {
  const copied = (id, kind) => workflowUi.workflowFromPattern({
    id, label: id, template: { nodes: [{ id: 'node', goal: '{{request}}', kind, deps: [] }] },
  }, 'small').nodes[0];
  assert.strictEqual(copied('classify-and-act', 'classify').continuation, 'route');
  assert.strictEqual(copied('adversarial-verification', 'verify').continuation, 'retry');
  assert.strictEqual(copied('loop-until-done', 'verify').continuation, 'retry');
  assert.strictEqual(copied('fan-out-and-synthesize', 'verify').continuation, undefined);
});

test('全工程の既定目的は依頼本文を複製せず、自然文で役割を説明する', () => {
  for (const kind of ['work', 'generate', 'classify', 'synthesize', 'verify', 'filter', 'judge', 'reduce', 'split', 'map']) {
    assert.ok(workflowUi.defaultGoal(kind).length > '{{request}}'.length, `${kind} の目的が必要です`);
    assert.doesNotMatch(workflowUi.defaultGoal(kind), /\{\{request\}\}/);
  }
  assert.match(workflowUi.defaultGoal('work'), /この工程で担当する作業/);
  assert.match(workflowUi.defaultGoal('verify'), /前の工程の成果/);
  assert.doesNotMatch(workflowUi.workflowFromPattern({
    id: 'candidate', label: '候補', template: { nodes: [
      { id: 'candidate', goal: '候補1: {{request}}', kind: 'generate', deps: [] },
    ] },
  }, 'small').nodes[0].goal, /\{\{request\}\}/);
});

test('system role を含む手法は工程セットへ変換しない', () => {
  const pattern = workflowUi.methodWorkflowPattern({
    id: 'plan-and-build', description: '計画して実装する',
    fragments: [{ role: 'planner', text: '計画する' }, { role: 'worker', text: '実装する' }],
  });
  assert.strictEqual(pattern, null);
});

test('複数工程の雛形を既存ノードの後へ接続済みで追加する', () => {
  const workflow = {
    version: 2, entry: ['before'], exit: ['before'], methods: [],
    nodes: [{ id: 'before', kind: 'work', tier: 'small', deps: [], x: 40, y: 70 }],
  };
  const added = workflowUi.insertPattern(workflow, {
    id: 'review-set', label: '生成して検証', template: { nodes: [
      { id: 'draft', goal: '生成', kind: 'generate', deps: [] },
      { id: 'check', goal: '検証', kind: 'verify', deps: ['draft'] },
    ] },
  }, 'small', 'before', { x: 320, y: 70 });
  assert.deepStrictEqual(added.map((node) => node.id), ['draft', 'check']);
  assert.deepStrictEqual(workflow.nodes.find((node) => node.id === 'draft').deps, ['before']);
  assert.deepStrictEqual(workflow.nodes.find((node) => node.id === 'check').deps, ['draft']);
  assert.deepStrictEqual(workflow.exit, [], '追加した末端を明示的に終了へつなぐまで未完了にする');
});

test('雛形カードは分岐を含む接続例を左から表す', () => {
  const columns = workflowUi.patternColumns({ template: { nodes: [
    { id: 'draft-a', kind: 'generate', deps: [] },
    { id: 'draft-b', kind: 'generate', deps: [] },
    { id: 'pick', kind: 'judge', deps: ['draft-a', 'draft-b'] },
  ] } });
  assert.deepStrictEqual(columns, [
    ['開始'], ['生成', '生成'], ['判定'], ['終了'],
  ]);
});

test('カードの表示名は agent-flow の全ノード種別と1対1に対応する', () => {
  const kinds = ['work', 'generate', 'classify', 'synthesize', 'verify',
    'filter', 'judge', 'reduce', 'split', 'map', 'human', 'extract', 'retrieve'];
  const workflow = { nodes: kinds.map((kind, index) => ({ id: kind, kind, x: index })) };
  assert.deepStrictEqual(workflowUi.workflowColumns(workflow), [
    ['開始'], ['作業'], ['生成'], ['分類'], ['統合'], ['検証'],
    ['選別'], ['判定'], ['集約'], ['分割'], ['個別処理'], ['人の確認'], ['抽出'], ['取得'], ['終了'],
  ]);
});

test('詳細グラフは編集種別を worker・verify・human の実行ロールへ対応付ける', () => {
  const kinds = ['work', 'generate', 'classify', 'synthesize', 'verify',
    'filter', 'judge', 'reduce', 'split', 'map', 'human', 'extract', 'retrieve'];
  assert.deepStrictEqual(kinds.map((kind) => workflowUi.nodePresentation({ kind }).role), [
    '作業', '作業', '作業', '作業', '検証', '作業', '作業', '作業', '作業', '作業',
    '人間', '作業', '作業',
  ]);
});

test('雛形カードのノードはノード種別名だけを表示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({ workflows: [], methods: [], patterns: [{
      id: 'classify-and-act', label: '分類して実行', description: '分類後に実行する',
      template: { nodes: [{ id: 'classify', kind: 'classify', deps: [] }] },
    }] });
    assert.match(html, /<i><b>分類<\/b><\/i>/);
    assert.match(html, /<i class="runtime"[^>]*><b>作業<\/b><\/i>/);
    assert.doesNotMatch(html, /<small>分類<\/small>|<small>専門作業<\/small>/);
  } finally {
    global.esc = previousEsc;
  }
});

test('雛形カードはノード種別名以外を重複表示しない', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({ workflows: [], methods: [], patterns: [{
      id: 'static', label: '作業して検証', description: '標準工程',
      template: { nodes: [
        { id: 'work', kind: 'work', deps: [] },
        { id: 'generate', kind: 'generate', deps: ['work'] },
        { id: 'verify', kind: 'verify', deps: ['generate'] },
      ] },
    }] });
    assert.doesNotMatch(html, /<b>作業<\/b><small>作業<\/small>/);
    assert.doesNotMatch(html, /<b>検証<\/b><small>検証<\/small>/);
    assert.doesNotMatch(html, /<b>作業<\/b><small>生成<\/small>/,
      'ロールと異なる工程名もカード内では省く');
  } finally {
    global.esc = previousEsc;
  }
});

test('雛形カードは実行時に増える工程と接続を破線表示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({ workflows: [], methods: [], patterns: [{
      id: 'map-reduce', label: '分割して集約', description: '動的に展開する',
      template: { nodes: [{ id: 'split', kind: 'split', deps: [] }] },
    }, {
      id: 'loop-until-done', label: '完了まで反復', description: '完了まで繰り返す',
      template: { nodes: [
        { id: 'work', kind: 'work', deps: [] },
        { id: 'verify', kind: 'verify', deps: ['work'] },
      ] },
    }] });
    assert.match(html, /<i><b>分割<\/b><\/i>/);
    assert.match(html, /<i class="runtime"[^>]*><b>個別処理<\/b><\/i>/);
    assert.match(html, /<i class="runtime"[^>]*><b>集約<\/b><\/i>/);
    assert.match(html, /wf-mini-edge runtime/);
    assert.match(html, /wf-loop-back runtime/);
  } finally {
    global.esc = previousEsc;
  }
});

test('雛形カードと編集開始時は同じノード構成を使う', () => {
  const pattern = { id: 'fan-out-and-synthesize', template: { nodes: [
    { id: 'a', kind: 'work', deps: [] }, { id: 'b', kind: 'work', deps: [] },
    { id: 'c', kind: 'work', deps: [] }, { id: 'd', kind: 'work', deps: [] },
    { id: 'join', kind: 'synthesize', deps: ['a', 'b', 'c', 'd'] },
  ] } };
  const workflow = workflowUi.workflowFromPattern(pattern, 'small');
  assert.strictEqual(workflow.nodes.filter((node) => node.kind === 'work').length, 3,
    'カードに表示する三並列と編集開始時の実ノード数を揃える');
  assert.deepStrictEqual(workflowUi.patternColumns(pattern),
    workflowUi.workflowColumns(workflowUi.visualWorkflow(workflow)));
  assert.deepStrictEqual(workflowUi.patternColumns(pattern),
    [['開始'], ['作業', '作業', '作業'], ['統合'], ['終了']]);
});

test('実行時に増える工程もカードと編集画面の両方へ同じ読み取り専用ノードで示す', () => {
  const mapPattern = { id: 'map-reduce', template: { nodes: [{ id: 'split', kind: 'split', deps: [] }] } };
  const mapVisual = workflowUi.visualWorkflow(workflowUi.workflowFromPattern(mapPattern, 'small'));
  assert.deepStrictEqual(workflowUi.patternColumns(mapPattern),
    [['開始'], ['分割'], ['個別処理', '個別処理', '個別処理'], ['集約'], ['終了']]);
  assert.strictEqual(mapVisual.nodes.filter((node) => node.runtime).length, 4);
  assert.deepStrictEqual(workflowUi.patternColumns(mapPattern), workflowUi.workflowColumns(mapVisual));

  const routePattern = {
    id: 'classify-and-act', template: { nodes: [{ id: 'classify', kind: 'classify', deps: [] }] },
  };
  const routeVisual = workflowUi.visualWorkflow(workflowUi.workflowFromPattern(routePattern, 'small'));
  assert.deepStrictEqual(workflowUi.patternColumns(routePattern), [['開始'], ['分類'], ['作業'], ['終了']]);
  assert.strictEqual(routeVisual.nodes.find((node) => node.runtime).label, '専門作業');
});

test('初期画面は保存済み・一から作る・雛形を同じカード導線にまとめる', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({
      workflows: [
        { id: 'saved', name: '保存済みフロー', description: '続きから編集する' },
        { id: 'design-interactive', name: '対話型設計', description: '内部設計フロー',
          purpose: 'design', libraryVisibility: 'internal' },
      ],
      patterns: [{
        id: 'verify', label: '検証つき', description: '作業後に検証する',
        template: { nodes: [{ id: 'work', kind: 'work', deps: [] }] },
      }],
      methods: [{
        id: 'build-review', description: '作業して検証する',
        fragments: [{ role: 'worker', text: '実装する' }, { role: 'verify', text: '検証する' }],
      }, {
        id: 'no-self-approval', description: '作成者と同じ呼び出しによる自己承認を避ける',
        fragments: [{ role: 'verify', text: '検証する' }, { role: 'evaluator', text: '判定する' }],
      }],
    });
    assert.match(html, /data-workflow-id="saved"/);
    assert.doesNotMatch(html, /data-workflow-id="design-interactive"|内部設計フロー/);
    assert.match(html, /id="wf-new"/);
    assert.match(html, /data-pattern-id="verify"/);
    assert.match(html, /data-method-pattern-id="build-review"/);
    assert.match(html, /作業ルール/);
    assert.doesNotMatch(html, /data-method-pattern-id="no-self-approval"/);
    assert.doesNotMatch(html, /この雛形から作る|wf-list/);
  } finally {
    global.esc = previousEsc;
  }
});

test('次の工程は接続元に合う候補を返す', () => {
  const workflow = { nodes: [
    { id: 'draft', kind: 'generate' },
    { id: 'check', kind: 'verify' },
  ] };
  assert.deepStrictEqual(workflowUi.recommendedKinds(workflow, '__start__'), ['work', 'retrieve', 'extract', 'human']);
  assert.deepStrictEqual(workflowUi.recommendedKinds(workflow, 'draft'), ['filter', 'judge', 'synthesize']);
  assert.deepStrictEqual(workflowUi.recommendedKinds(workflow, 'check'), ['work', 'verify']);
});

test('分岐先の追加は既存ノードと重ならない位置を選ぶ', () => {
  const workflow = { nodes: [
    { id: 'draft', x: 300, y: 70 },
    { id: 'check', x: 570, y: 70 },
  ] };
  assert.deepStrictEqual(workflowUi.nextNodePosition(workflow, 'draft'), { x: 580, y: 210 });
});

test('作業ルールは工程と処理をまとめ、料金区分を数値で見せない', () => {
  const view = workflowUi.methodPresentation({
    fragments: [{ role: 'planner', text: '最初に計画する' }, { role: 'worker', text: '小さく実装する' }],
    when: { tiers: ['large'], purposes: ['work'], max_relative_cost: 0 },
  });
  assert.deepStrictEqual(view.roles, ['計画', '作業']);
  assert.deepStrictEqual(view.target, ['作業']);
  assert.strictEqual(view.condition, '高性能向け・ローカル実行向け');
  assert.deepStrictEqual(['basic', 'small', 'medium', 'large', 'auto'].map(workflowUi.tierLabel),
    ['単純作業', '軽量', '標準', '高性能', '自動']);
});

test('完了まで反復の雛形は説明を添えず検証ノードから作業ノードへ矢印をつなぐ', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({
      workflows: [], methods: [], patterns: [{
        id: 'loop-until-done', label: '完了まで反復', description: '完了まで繰り返す',
        template: { nodes: [
          { id: 'work', kind: 'work', deps: [] },
          { id: 'verify', kind: 'verify', deps: ['work'] },
        ] },
      }],
    });
    assert.match(html, /wf-loop-back/);
    assert.match(html, /wf-loop-back runtime" style="grid-column:3 \/ 6;grid-row:2"/);
    assert.match(html, /<path d="M80 0v18H20V0"><\/path><path d="m16 4 4-4 4 4"><\/path>/);
    assert.doesNotMatch(html, /wf-loop-direction|<small>未完了なら作業へ戻る<\/small>/);
  } finally {
    global.esc = previousEsc;
  }
});

test('生成して検証の雛形も注釈なしの戻り線だけで反復を示す', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({
      workflows: [], methods: [], patterns: [{
        id: 'adversarial-verification', label: '生成して検証', description: '生成後に検証する',
        template: { nodes: [
          { id: 'work', kind: 'generate', deps: [] },
          { id: 'verify', kind: 'verify', deps: ['work'] },
        ] },
      }],
    });
    assert.match(html, /wf-loop-back runtime/);
    assert.doesNotMatch(html, /<small>問題があれば生成へ戻る<\/small>/);
  } finally {
    global.esc = previousEsc;
  }
});


test('接続線の経路はノード座標の変更に追従する', () => {
  const source = { x: 40, y: 70 };
  const target = { x: 320, y: 70 };
  const before = workflowUi.edgePath(source, target);
  source.x = 180;
  assert.notStrictEqual(workflowUi.edgePath(source, target), before);
});

test('接続判定は方向・重複・循環・split を同じ規則で拒否する', () => {
  const workflow = {
    version: 2, entry: ['a'], exit: ['c'],
    nodes: [
      { id: 'a', kind: 'work', deps: [] },
      { id: 'b', kind: 'work', deps: ['a'] },
      { id: 'c', kind: 'work', deps: ['b'] },
      { id: 'split', kind: 'split', deps: [] },
    ],
  };
  assert.match(workflowUi.connectionError(workflow, 'a', 'a'), /自身/);
  assert.match(workflowUi.connectionError(workflow, 'a', 'b'), /接続済み/);
  assert.match(workflowUi.connectionError(workflow, 'c', 'a'), /循環/);
  assert.match(workflowUi.connectionError(workflow, 'split', 'c'), /split/);
  assert.match(workflowUi.connectionError(workflow, '__end__', 'a'), /終了/);
  assert.strictEqual(workflowUi.connectionError(workflow, 'split', '__end__'), '');
});

test('開始・通常ノード・終了の接続と解除を同じ操作で更新する', () => {
  const workflow = {
    version: 2, entry: [], exit: [],
    nodes: [
      { id: 'a', kind: 'work', deps: [] },
      { id: 'b', kind: 'verify', deps: [] },
    ],
  };
  workflowUi.connectWorkflow(workflow, '__start__', 'a');
  workflowUi.connectWorkflow(workflow, 'a', 'b');
  workflowUi.connectWorkflow(workflow, 'b', '__end__');
  assert.deepStrictEqual(workflow.entry, ['a']);
  assert.deepStrictEqual(workflow.nodes[1].deps, ['a']);
  assert.deepStrictEqual(workflow.exit, ['b']);
  workflowUi.disconnectWorkflow(workflow, 'a', 'b');
  assert.deepStrictEqual(workflow.nodes[1].deps, []);
  assert.throws(() => workflowUi.connectWorkflow(workflow, 'b', 'b'), /自身/);
});

test('設定画面では定期更新しない', () => {
  const previousDocument = global.document;
  const previousApi = global.api;
  let calls = 0;
  global.document = {
    getElementById: (id) => ({
      classList: { contains: (name) => id === 'tab-workflow-settings' && name === 'active' },
    }),
  };
  global.api = { adhocFlowOverview: () => { calls += 1; return new Promise(() => {}); } };
  workflowUi.refresh();
  assert.strictEqual(calls, 0);
  global.document = previousDocument;
  global.api = previousApi;
});

test('表示済みの設定画面は全体更新から再描画しない', () => {
  const previousDocument = global.document;
  const previousEsc = global.esc;
  const previousOverview = workflowUi._state.overview;
  const previousEditor = workflowUi._state.editor;
  let writes = 0;
  const pane = {
    firstElementChild: {},
    classList: { contains: (name) => name === 'active' },
    querySelectorAll: () => [],
    querySelector: () => null,
    get innerHTML() { return '<section>編集中</section>'; },
    set innerHTML(_value) { writes += 1; },
  };
  global.esc = (value) => String(value);
  global.document = { getElementById: (id) => (id === 'tab-workflow-settings' ? pane : null) };
  workflowUi._state.overview = { workflows: [], patterns: [] };
  workflowUi._state.editor = null;
  try {
    workflowUi.render();
    assert.strictEqual(writes, 0);
  } finally {
    workflowUi._state.overview = previousOverview;
    workflowUi._state.editor = previousEditor;
    global.document = previousDocument;
    global.esc = previousEsc;
  }
});

test('バックログ用フロースナップショットは自動・標準・カスタムを固定する', () => {
  const cfg = { adhocFlow: { workflowDir: tmpdir('workflow-snapshot-') } };
  const workflow = adhoc.saveWorkflow(cfg, {
    name: '分類して実行',
    nodes: [{ id: 'a', goal: '分類', kind: 'classify', tier: 'large', continuation: 'route' }],
  });
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'gpt-5' });
  try {
    assert.deepStrictEqual(adhoc.snapshotSelection(cfg, null), { version: 1, type: 'auto' });
    assert.deepStrictEqual(adhoc.snapshotSelection(cfg, { type: 'pattern', id: 'map-reduce' }),
      { version: 1, type: 'pattern', pattern: 'map-reduce' });
    const custom = adhoc.snapshotSelection(cfg, { type: 'custom', id: workflow.id });
    assert.strictEqual(custom.type, 'custom');
    assert.strictEqual(custom.evaluate, true);
    assert.deepStrictEqual(custom.nodes[0].agent, { agent_cli: 'codex', model: 'gpt-5' });
    assert.throws(() => adhoc.snapshotSelection(cfg, { type: 'custom', id: 'missing' }), /見つかりません/);
    assert.throws(() => adhoc.snapshotSelection(cfg, { type: 'bad' }), /不正/);
  } finally {
    profiles.resolveTier = original;
  }
});

test('Git cwd は Windows パスを WSL に変換して workspace 契約へする', () => {
  const original = exec.shInWsl;
  let command = '';
  exec.shInWsl = (line) => {
    command = line;
    return { status: 0, stdout: '/mnt/c/dev/repo\nmain', stderr: '' };
  };
  try {
    assert.deepStrictEqual(adhoc.gitWorkspace({}, 'C:\\dev\\repo'), {
      url: '/mnt/c/dev/repo', base: 'main', path: '', desc: 'workflow',
    });
    assert.ok(command.includes("'/mnt/c/dev/repo'"));
  } finally {
    exec.shInWsl = original;
  }
});

test('標準フローカタログは agent-flow の JSON だけを受理する', () => {
  const original = exec.shInWsl;
  try {
    exec.shInWsl = () => ({ status: 0, stdout: '[{"id":"map-reduce","label":"分割"}]' });
    assert.strictEqual(adhoc.patternCatalog({}).length, 1);
    exec.shInWsl = () => ({ status: 0, stdout: 'broken' });
    assert.deepStrictEqual(adhoc.patternCatalog({}), []);
    exec.shInWsl = () => ({ status: 1, stdout: '' });
    assert.deepStrictEqual(adhoc.patternCatalog({}), []);
  } finally {
    exec.shInWsl = original;
  }
});

test('バックログの承認コマンドにフロースナップショットを載せる', () => {
  const projectDir = tmpdir('workflow-command-');
  const flow = { version: 1, type: 'pattern', pattern: 'map-reduce' };
  const result = actions.dropCommand(projectDir, {
    action: 'approve', id: 'T1', reason: '実行', flow,
  });
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(result.file, 'utf8')).flow, flow);
});

// --- プリセットの整形 ---------------------------------------------------------

test('normalizePreset が形を整える（name 必須・ノードの id/goal 必須・重複拒否）', () => {
  const p = adhoc.normalizePreset({
    name: '調査',
    nodes: [
      { id: 'a', goal: 'g1', kind: 'work' },
      { id: 'b', goal: 'g2', kind: 'verify', deps: ['a'], agentCli: 'ollama', model: 'm' },
    ],
    methods: ['adversarial-verify'],
    evaluate: true,
  });
  assert.strictEqual(p.name, '調査');
  assert.strictEqual(p.nodes.length, 2);
  assert.deepStrictEqual(p.nodes[1].deps, ['a']);
  assert.strictEqual(p.nodes[1].agentCli, 'ollama');
  assert.strictEqual(p.evaluate, true);
  assert.ok(p.id.startsWith('preset-'));

  assert.throws(() => adhoc.normalizePreset({ name: '' }), /プリセット名/);
  assert.throws(() => adhoc.normalizePreset({ name: 'x', nodes: [{ id: 'a' }] }), /id と goal/);
  assert.throws(
    () => adhoc.normalizePreset({
      name: 'x',
      nodes: [{ id: 'a', goal: 'g' }, { id: 'a', goal: 'h' }],
    }),
    /重複/
  );
});

test('planFromPreset が投入契約の plan（per-node agent 含む）へ変換する', () => {
  const p = adhoc.normalizePreset({
    name: 'F',
    evaluate: true,
    nodes: [
      { id: 'a', goal: '調査: {{request}}' },
      { id: 'j', goal: '判定', kind: 'judge', deps: ['a'], agentCli: 'codex', model: 'gpt-5' },
    ],
  });
  const plan = adhoc.planFromPreset(p);
  assert.strictEqual(plan.name, 'F');
  assert.strictEqual(plan.evaluate, true);
  assert.deepStrictEqual(plan.nodes[1].agent, { agent_cli: 'codex', model: 'gpt-5' });
  assert.strictEqual(plan.nodes[0].agent, undefined);
  // ノード無し（手法だけのプリセット）は plan を作らない＝planner に任せる
  assert.strictEqual(adhoc.planFromPreset(adhoc.normalizePreset({ name: 'x' })), null);
});

// --- 手法スナップショット（複製 + source hash） -------------------------------

function methodsFixture() {
  const methodsDir = tmpdir('adhoc-methods-');
  const tuningDir = tmpdir('adhoc-tuning-');
  const catalogMethod = {
    id: 'adversarial-verify',
    description: '壊す側に立って検証する',
    enabled: false,
    fragments: [{ role: 'verify', text: '反例を探してから判定してください。' }],
    when: { tiers: ['small'] },
    origin: 'local:test',
  };
  fs.writeFileSync(
    path.join(methodsDir, 'adversarial-verify.json'), JSON.stringify(catalogMethod)
  );
  const cfg = {
    orchestration: { methodsDir, tuningDir },
    adhocFlow: { tuningRoot: tmpdir('adhoc-run-tuning-') },
  };
  return { cfg, catalogMethod };
}

test('methodsSnapshot がカタログ手法を複製し source: methods/<id>@<hash> を刻む', () => {
  const { cfg, catalogMethod } = methodsFixture();
  const snap = adhoc.methodsSnapshot(cfg, ['adversarial-verify']);
  assert.strictEqual(snap.length, 1);
  assert.strictEqual(snap[0].enabled, true);
  assert.strictEqual(snap[0].source, `methods/adversarial-verify@${tuning.sourceHash(catalogMethod)}`);
  assert.throws(() => adhoc.methodsSnapshot(cfg, ['no-such-method']), /見つかりません/);
  assert.strictEqual(adhoc.methodsSnapshot(cfg, []), null);
});

test('writeRunTuning が run 専用の agent-tuning スナップショットを書く', () => {
  const { cfg } = methodsFixture();
  const snap = adhoc.methodsSnapshot(cfg, ['adversarial-verify']);
  const dir = adhoc.writeRunTuning(cfg, 'adhoc-test-1', snap);
  const data = JSON.parse(fs.readFileSync(path.join(dir, 'tuning.json'), 'utf8'));
  assert.strictEqual(data.version, 1);
  assert.strictEqual(data.revision, 1);
  assert.strictEqual(data.methods[0].id, 'adversarial-verify');
  // external-facing の「注入ゼロ」制約（文体圧縮の漏れ防止）を run 専用でも保つ
  assert.deepStrictEqual(data.profiles['external-facing'], { injections: [] });
});

// --- 起動シェル行 -------------------------------------------------------------

test('buildLaunchLine が inbox 起動・手法 env・エンジン既定のフラグを組む', () => {
  const line = adhoc.buildLaunchLine({}, {
    runId: 'adhoc-1', busDir: '/tmp/bus', tuningDir: '/tmp/tun',
    agentCli: 'ollama', model: 'qwen3.5:9b', planner: 'stub',
  });
  assert.ok(line.includes("--bus '/tmp/bus'"));
  assert.ok(line.includes("--run-id 'adhoc-1' run --from-inbox"));
  assert.ok(line.includes("AGENT_TUNING_DIR='/tmp/tun'"));
  assert.ok(line.includes("--planner 'stub'"));
  assert.ok(line.includes("--agent-cli 'ollama'"));
  assert.ok(line.includes("--model 'qwen3.5:9b'"));
  assert.ok(line.includes('command -v agent-flow'), 'PATH 利用時は存在確認ガードを入れる');
  assert.ok(line.includes('nohup'), 'dashboard の生存に依存させない（C6）');

  const custom = adhoc.buildLaunchLine(
    { adhocFlow: { agentFlowCommand: 'python3 /x/agent-flow.py' } },
    { runId: 'r', busDir: '/b', tuningDir: null }
  );
  assert.ok(custom.includes('python3 /x/agent-flow.py'));
  assert.ok(!custom.includes('command -v'), '明示コマンド指定時はガード不要');
  assert.ok(!custom.includes('AGENT_TUNING_DIR'), '手法未選択なら端末の tuning を置換しない');
});

test('IPC: 同梱カタログとリポジトリ配布の作業ルールが同 id なら、設定画面もリポジトリを優先する', () => {
  const { cfg, catalogMethod } = methodsFixture();
  const repo = tmpdir('adhoc-overview-methods-repo-');
  fs.mkdirSync(path.join(repo, '.git'));
  const methodsDir = path.join(repo, '.agents', 'methods');
  fs.mkdirSync(methodsDir, { recursive: true });
  const override = { ...catalogMethod, description: 'リポジトリ版の説明',
    fragments: [{ role: 'verify', text: 'このリポジトリでは3件以上の反例を探す。' }] };
  fs.writeFileSync(path.join(methodsDir, `${catalogMethod.id}.json`), JSON.stringify(override));
  const originalRoots = projectEngine.projectRoots;
  projectEngine.projectRoots = () => [repo];
  try {
    const handlers = {};
    require('../src/features/adhoc-flow/main/ipc.js').registerIpc({
      handle: (ch, fn) => { handlers[ch] = fn; }, loadConfig: () => cfg, saveConfig: () => cfg,
    });
    const ov = handlers['adhocFlow:overview']({ cwd: repo });
    const matches = ov.methodsCatalog.filter((m) => m.id === catalogMethod.id);
    assert.strictEqual(matches.length, 1, '同 id は1枚のカードにする（衝突時に2枚並べない）');
    assert.strictEqual(matches[0].storage, 'registered-folder');
    assert.strictEqual(matches[0].description, 'リポジトリ版の説明');
  } finally {
    projectEngine.projectRoots = originalRoots;
  }
});

test('IPC: 作業準備項目を作成して対象別に一覧できる', () => {
  const dir = tmpdir('preparation-ipc-');
  const cfg = { preparationDir: dir, adhocFlow: {} };
  try {
    const handlers = {};
    require('../src/features/adhoc-flow/main/ipc.js').registerIpc({
      handle: (channel, fn) => { handlers[channel] = fn; }, loadConfig: () => cfg, saveConfig: () => cfg,
    });
    const created = handlers['preparation:create']({
      target: 'workflow', title: 'README更新', goal: 'README更新', route: 'direct',
    });
    const listed = handlers['preparation:list']({ target: 'workflow' });
    assert.deepStrictEqual({ route: created.item.route, phase: created.item.phase,
      ids: listed.items.map((item) => item.id) }, {
      route: 'direct', phase: 'implementation-ready', ids: [created.item.id],
    });
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

// --- 投入（submit_request 契約の投函 + 起動） ---------------------------------

test('submit が submit_request 契約を投函し plan と手法を運ぶ', () => {
  const { cfg } = methodsFixture();
  cfg.adhocFlow.busDir = tmpdir('adhoc-bus-');
  const calls = [];
  const orig = exec.shInWsl;
  exec.shInWsl = (line, timeout, distro) => {
    calls.push({ line, timeout, distro });
    return { ok: true, status: 0, stdout: 'launched:1', stderr: '', error: '' };
  };
  try {
    const res = adhoc.submit(cfg, {
      request: 'X を調べてまとめる',
      preset: {
        name: '二段検証',
        nodes: [
          { id: 'a', goal: '調査: {{request}}' },
          { id: 'v', goal: '検証', kind: 'verify', deps: ['a'] },
        ],
        methods: ['adversarial-verify'],
      },
    });
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${res.runId}.json`), 'utf8'));
    assert.strictEqual(rec.request, 'X を調べてまとめる');
    assert.strictEqual(rec.submitter, adhoc.SUBMITTER);
    assert.strictEqual(rec.workspace, null, 'アドホックは読み取り専用 run（書込先なし）');
    assert.strictEqual(rec.plan.name, '二段検証');
    assert.strictEqual(rec.plan.nodes.length, 2);
    assert.ok(res.runId.startsWith('adhoc-'));
    assert.deepStrictEqual(res.methods, ['adversarial-verify']);
    assert.ok(res.tuningDir && fs.existsSync(path.join(res.tuningDir, 'tuning.json')));
    assert.strictEqual(calls.length, 1);
    assert.ok(calls[0].line.includes(`--run-id '${res.runId}' run --from-inbox`));
    assert.throws(() => adhoc.submit(cfg, { request: '   ' }), /要求テキスト/);
  } finally {
    exec.shInWsl = orig;
  }
});

test('カスタムフローは旧全体ルールを保存・実行しない', () => {
  const { cfg } = methodsFixture();
  cfg.adhocFlow.busDir = tmpdir('adhoc-custom-method-bus-');
  cfg.adhocFlow.workflowDir = tmpdir('adhoc-custom-method-workflow-');
  const originalExec = exec.shInWsl;
  const originalTier = profiles.resolveTier;
  exec.shInWsl = () => ({ status: 0, stdout: 'launched:1', stderr: '' });
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'gpt-5' });
  try {
    const workflow = adhoc.saveWorkflow(cfg, {
      name: '全体手法つき', methods: ['adversarial-verify'],
      nodes: [
        { id: 'a', goal: '{{request}}', tier: 'small' },
        { id: 'v', goal: '完了条件を確認', kind: 'verify', tier: 'medium', deps: ['a'], continuation: 'retry' },
      ],
    });
    assert.strictEqual(workflow.methods, undefined);
    const result = adhoc.submit(cfg, {
      request: '検証する', selection: { type: 'custom', id: workflow.id },
    });
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${result.runId}.json`), 'utf8'));
    assert.strictEqual(rec.plan.evaluate, true, '継続動作を agent-flow の評価契約へ渡す');
    assert.deepStrictEqual(result.methods, []);
    assert.strictEqual(result.tuningDir, null);
  } finally {
    exec.shInWsl = originalExec;
    profiles.resolveTier = originalTier;
  }
});

test('一貫性ゲート: verify-plan で組んだ検証計画を inbox へ運ぶ', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('adhoc-coherence-') } };
  const plan = {
    version: 1, task_id: 'x', workspace: '/repo',
    commands: [{ command: adhoc.COHERENCE_COMMAND, source: 'user' }],
    criteria: [], digest: 'sha256:abc',
  };
  const calls = [];
  const orig = exec.shInWsl;
  exec.shInWsl = (line) => {
    calls.push(line);
    if (line.startsWith('root=')) return { status: 0, stdout: '/repo\nmain' };
    if (line.includes('verify-plan')) return { status: 0, stdout: `${JSON.stringify(plan)}\n` };
    return { status: 0, stdout: 'launched:1', stderr: '' };
  };
  try {
    const res = adhoc.submit(cfg, { request: '整合を保って直す', cwd: '/repo', coherenceGate: true });
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${res.runId}.json`), 'utf8'));
    assert.deepStrictEqual(rec.verification_plan, plan, '返った JSON をそのまま運ぶ（複製実装しない）');
    const built = calls.find((line) => line.includes('verify-plan'));
    assert.ok(built.includes(`--task-id '${res.runId}'`));
    assert.ok(built.includes('codd-gate verify --base'));
    // ゲート無しの投入は verification_plan を書かない
    const plain = adhoc.submit(cfg, { request: '整合を保って直す', cwd: '/repo' });
    const plainRec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${plain.runId}.json`), 'utf8'));
    assert.strictEqual(plainRec.verification_plan, undefined);
  } finally {
    exec.shInWsl = orig;
  }
});

test('一貫性ゲート: フェイルクローズ（組み立て失敗・リポジトリ未選択で起動しない）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('adhoc-coherence-fc-') } };
  const orig = exec.shInWsl;
  exec.shInWsl = (line) => {
    if (line.startsWith('root=')) return { status: 0, stdout: '/repo\nmain' };
    if (line.includes('verify-plan')) return { status: 2, stdout: '', stderr: 'agent-flow が古い' };
    return { status: 0, stdout: 'launched:1', stderr: '' };
  };
  try {
    assert.throws(() => adhoc.submit(cfg, { request: 'x', cwd: '/repo', coherenceGate: true }),
      /一貫性ゲートの検証計画を組み立てられませんでした/);
    assert.throws(() => adhoc.submit(cfg, { request: 'x', coherenceGate: true }),
      /Git リポジトリの選択が必要/);
    // 失敗した投入は inbox に要求を残さない（書く前に止まる）
    assert.deepStrictEqual(
      fs.existsSync(path.join(cfg.adhocFlow.busDir, 'inbox'))
        ? fs.readdirSync(path.join(cfg.adhocFlow.busDir, 'inbox')) : [],
      []);
  } finally {
    exec.shInWsl = orig;
  }
});

test('submit はプリセット無しでも投入できる（planner に任せる）', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('adhoc-bus2-') } };
  const orig = exec.shInWsl;
  exec.shInWsl = () => ({ ok: true, status: 0, stdout: '', stderr: '', error: '' });
  try {
    const res = adhoc.submit(cfg, { request: '軽い調べ物' });
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${res.runId}.json`), 'utf8'));
    assert.strictEqual(rec.plan, undefined);
    assert.strictEqual(res.tuningDir, null);
  } finally {
    exec.shInWsl = orig;
  }
});

test('実行時指定は tier を候補へ解決して inbox に固定し、再実行にも引き継ぐ', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('adhoc-run-overrides-') } };
  const originalExec = exec.shInWsl;
  const originalTier = profiles.resolveTier;
  exec.shInWsl = () => ({ status: 0, stdout: 'launched:1', stderr: '' });
  profiles.resolveTier = (_cfg, tier) => ({ agent_cli: 'codex', model: `model-${tier}` });
  try {
    const first = adhoc.submit(cfg, {
      request: '実装する',
      executionOverrides: {
        version: 1,
        mode: 'saving',
        roles: { worker: { tier: 'medium' } },
        kinds: { verify: { agent_cli: 'claude', model: 'opus' } },
      },
    });
    const firstInbox = adhoc.readInbox(cfg.adhocFlow.busDir, first.runId);
    assert.strictEqual(firstInbox.execution_overrides.mode, 'saving');
    assert.deepStrictEqual(firstInbox.execution_overrides.roles.worker, {
      tier: 'medium', agent_cli: 'codex', model: 'model-medium', source: 'run-role', pinned: true,
    });
    assert.deepStrictEqual(firstInbox.execution_overrides.kinds.verify, {
      agent_cli: 'claude', model: 'opus', source: 'run-kind', pinned: true,
    });
    const second = adhoc.resubmit(cfg, first.runId);
    assert.deepStrictEqual(
      adhoc.readInbox(cfg.adhocFlow.busDir, second.runId).execution_overrides,
      firstInbox.execution_overrides,
    );
    assert.throws(() => adhoc.normalizeExecutionOverrides(cfg, {
      version: 1, kinds: { work: { tier: 'basic' } },
    }), /任せられません/);
  } finally {
    exec.shInWsl = originalExec;
    profiles.resolveTier = originalTier;
  }
});

test('submit が Git cwd と標準フローを inbox に固定する', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('workflow-bus-') } };
  const original = exec.shInWsl;
  exec.shInWsl = (line) => (line.includes('rev-parse --show-toplevel')
    ? { status: 0, stdout: '/repo\nmain', stderr: '' }
    : { status: 0, stdout: 'launched:1', stderr: '' });
  try {
    const result = adhoc.submit(cfg, {
      request: '実装する', cwd: '/repo', selection: { type: 'pattern', id: 'map-reduce' },
    });
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${result.runId}.json`), 'utf8'));
    assert.deepStrictEqual(rec.workspace, { url: '/repo', base: 'main', path: '', desc: 'workflow' });
    assert.strictEqual(rec.pattern, 'map-reduce');
    assert.strictEqual(rec.plan, undefined);
    assert.strictEqual(result.branch, `af/${result.runId}`);
  } finally {
    exec.shInWsl = original;
  }
});

test('起動失敗（agent-flow 不在）は投函後でもエラーとして返す', () => {
  const cfg = { adhocFlow: { busDir: tmpdir('adhoc-bus3-') } };
  const orig = exec.shInWsl;
  exec.shInWsl = () => ({ ok: false, status: 127, stdout: '', stderr: 'agent-flow-not-found', error: '' });
  try {
    assert.throws(() => adhoc.submit(cfg, { request: 'x' }), /起動に失敗/);
  } finally {
    exec.shInWsl = orig;
  }
});

test('resubmit が inbox 記録（plan 込み）と手法スナップショットを新 run へ写す', () => {
  const { cfg } = methodsFixture();
  cfg.adhocFlow.busDir = tmpdir('adhoc-bus4-');
  const orig = exec.shInWsl;
  exec.shInWsl = () => ({ ok: true, status: 0, stdout: '', stderr: '', error: '' });
  try {
    const first = adhoc.submit(cfg, {
      request: 'r',
      preset: { name: 'F', nodes: [{ id: 'a', goal: 'g' }], methods: ['adversarial-verify'] },
    });
    const second = adhoc.resubmit(cfg, first.runId);
    assert.notStrictEqual(second.runId, first.runId);
    const rec = JSON.parse(fs.readFileSync(
      path.join(cfg.adhocFlow.busDir, 'inbox', `${second.runId}.json`), 'utf8'));
    assert.strictEqual(rec.plan.name, 'F');
    const tuningFile = path.join(adhoc.runTuningDir(cfg, second.runId), 'tuning.json');
    assert.ok(fs.existsSync(tuningFile), '手法スナップショットも新 run へ写る');
    assert.throws(() => adhoc.resubmit(cfg, 'no-such-run'), /inbox 記録/);
  } finally {
    exec.shInWsl = orig;
  }
});

test('保持期限の掃除は終端済み・未対応なしの古い run だけを削除する', () => {
  const busDir = tmpdir('adhoc-retention-');
  const tuningRoot = tmpdir('adhoc-retention-tuning-');
  const cfg = { adhocFlow: { busDir, tuningRoot, retentionDays: 7 } };
  const now = Date.parse('2026-08-12T00:00:00Z');
  const makeRun = (id, status, updated, interaction) => {
    const runDir = path.join(busDir, 'runs', id);
    fs.mkdirSync(path.join(runDir, 'interactions'), { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({ status, updated_at: updated }));
    if (interaction) {
      const dir = path.join(runDir, 'interactions', 'ix-1');
      fs.mkdirSync(path.join(dir, 'responses'), { recursive: true });
      fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify(interaction));
    }
    fs.mkdirSync(path.join(busDir, 'inbox'), { recursive: true });
    fs.writeFileSync(path.join(busDir, 'inbox', `${id}.json`), '{}');
    fs.mkdirSync(path.join(tuningRoot, id), { recursive: true });
  };
  makeRun('old-done', 'done', '2026-08-01T00:00:00Z');
  makeRun('old-running', 'running', '2026-08-01T00:00:00Z');
  makeRun('old-waiting', 'done', '2026-08-01T00:00:00Z', {
    interaction_id: 'ix-1', expires_at: '2026-09-01T00:00:00Z',
  });
  makeRun('recent-done', 'done', '2026-08-10T00:00:00Z');
  assert.deepStrictEqual(adhoc.sweepExpiredRuns(cfg, now), ['old-done']);
  assert.ok(!fs.existsSync(path.join(busDir, 'runs', 'old-done')));
  assert.ok(!fs.existsSync(path.join(busDir, 'inbox', 'old-done.json')));
  assert.ok(!fs.existsSync(path.join(tuningRoot, 'old-done')));
  for (const id of ['old-running', 'old-waiting', 'recent-done']) {
    assert.ok(fs.existsSync(path.join(busDir, 'runs', id)), `${id} は保持する`);
  }
});

// --- 設計セッション: 短い要望 → 実行できる設計書 -------------------------------

test('旧設計セッションは workflow/new の共通契約として読み取れる', () => {
  const session = design.normalizeSession({
    version: 1, id: 'ds-old', goal: '既存要望', document: '## 目的\n既存設計', rounds: [],
  });
  assert.strictEqual(session.version, 2);
  assert.strictEqual(session.target, 'workflow');
  assert.strictEqual(session.sourceMode, 'new');
  assert.deepStrictEqual(session.sources, []);
  assert.strictEqual(session.proposal, null);
  assert.strictEqual(session.application, null);
});

test('設計ラウンド入力は型付きソースの意味と本文を保つ', () => {
  const request = design.buildRoundRequest({
    goal: '次の計画を作る',
    sources: [
      { id: 'master', kind: 'master', name: 'charter.md', content: '## constraints\n- Node 24' },
      { id: 'note-1', kind: 'note', name: 'idea.md', content: '監視も気になる' },
    ],
  });
  assert.ok(request.includes('## 設計材料'));
  assert.ok(request.includes('### master: charter.md'));
  assert.ok(request.includes('### note: idea.md'));
  assert.ok(request.includes('監視も気になる'));
});

test('実行前チェックは必須4節を言い換えごと決定的に数える（実行は止めない）', () => {
  assert.deepStrictEqual(workflowUi.readinessCheck(''), { empty: true, missing: ['目的', '変更対象', '受入基準', '検証方法'] });
  assert.deepStrictEqual(workflowUi.readinessCheck('## 目的\nx\n## 変更対象\ny\n## 受入基準\nz\n## 検証方法\nw'),
    { empty: false, missing: [] });
  // 言い換え・見出しレベル・「目的:」形式のいずれでも同じ節として数える
  assert.deepStrictEqual(workflowUi.readinessCheck('# 1. 狙い\nx\n#### スコープ\ny\n**完了条件**: z\nテスト方法: w'),
    { empty: false, missing: [] });
  assert.deepStrictEqual(workflowUi.readinessCheck('## 目的\nx\n## 変更対象\ny').missing, ['受入基準', '検証方法']);
  assert.deepStrictEqual(workflowUi.readinessCheck(workflowUi.REQUEST_TEMPLATE).missing, []);
});

test('ラウンド成果は「## 質問」節で設計書と質問へ分ける（見出し無しは収束）', () => {
  const withQuestions = design.splitDesignOutput(
    '## 目的\nやること\n\n## 質問\n1. 対象は A と B のどちらか\n   推奨: A\n2. 期限はあるか\n'
  );
  assert.strictEqual(withQuestions.document, '## 目的\nやること');
  assert.deepStrictEqual(withQuestions.questions, ['対象は A と B のどちらか\n推奨: A', '期限はあるか']);
  // 見出しが無ければ質問なし＝収束。全文が設計書になる
  assert.deepStrictEqual(design.splitDesignOutput('## 目的\nやること'),
    { document: '## 目的\nやること', questions: [] });
  // 見出しはあるのに項目を取れないときは切り落とさず全文を残す（劣化しても読める）
  const broken = design.splitDesignOutput('## 目的\nやること\n\n## 質問\n特にありません');
  assert.ok(broken.document.includes('特にありません'));
  assert.deepStrictEqual(broken.questions, []);
  assert.deepStrictEqual(design.splitDesignOutput(''), { document: '', questions: [] });
});

test('ラウンドへの入力は元の要望・現在の設計書・回答済みの質問だけを運ぶ', () => {
  const request = design.buildRoundRequest({
    goal: '依頼欄に設計書を読み込めるようにする',
    document: '## 目的\n既存の設計',
    answers: [{ question: '対象は', answer: 'A' }, { question: '期限は', answer: '  ' }],
  });
  assert.ok(request.includes('## 元の要望\n依頼欄に設計書を読み込めるようにする'));
  assert.ok(request.includes('## 現在の設計書\n## 目的\n既存の設計'));
  assert.ok(request.includes('1. 対象は\n   → A'));
  assert.ok(!request.includes('期限は'), '未回答の質問は運ばない');
  // 初回は設計書も回答も無い
  assert.strictEqual(design.buildRoundRequest({ goal: 'やりたいこと' }), '## 元の要望\nやりたいこと');
});

test('設計書は末端ノードの出力から取る（final.summary は抜粋なので使わない）', () => {
  const run = { nodes: {
    a: { id: 'a', deps: [], state: 'done', output: '要件', finishedAt: '2026-08-13T00:00:00Z' },
    b: { id: 'b', deps: ['a'], state: 'done', output: '## 目的\n設計書', finishedAt: '2026-08-13T00:01:00Z' },
  } };
  assert.strictEqual(design.sinkOutput(run), '## 目的\n設計書');
  assert.strictEqual(design.sinkOutput({ nodes: {} }), '');
});

test('同梱設計フローは内部設計用として分類する', () => {
  const builtin = adhoc.listWorkflows({ adhocFlow: {} })
    .filter((item) => ['design-auto', 'design-interactive'].includes(item.id));
  assert.strictEqual(builtin.length, 2);
  assert.ok(builtin.every((item) => item.purpose === 'design' && item.libraryVisibility === 'internal'));
});

test('設計セッションはラウンドごとに設計 run を投げ、成果を取り込む', () => {
  const busDir = tmpdir('design-bus-');
  const cfg = { adhocFlow: {
    busDir,
    workflowDir: tmpdir('design-workflow-'),
    designSessionDir: tmpdir('design-sessions-'),
    tuningRoot: tmpdir('design-tuning-'),
  } };
  const originalExec = exec.shInWsl;
  const originalTier = profiles.resolveTier;
  exec.shInWsl = () => ({ status: 0, stdout: 'launched:1', stderr: '' });
  profiles.resolveTier = () => ({ agent_cli: 'claude', model: '' });
  try {
    // 同梱フローが読めていること（対話・全自動の 2 本）
    const builtin = adhoc.listWorkflows(cfg).filter((item) => item._scope === 'builtin');
    assert.deepStrictEqual(builtin.map((item) => item.id).sort(), ['design-auto', 'design-interactive']);
    assert.throws(() => adhoc.saveWorkflow(cfg, { ...builtin[0], name: '変更' }), /読み取り専用/);

    const started = design.startRound(cfg, { goal: '依頼欄に設計書を読み込めるようにする', mode: 'interactive' });
    assert.strictEqual(started.runStatus, 'running');
    assert.strictEqual(started.rounds.length, 1);
    const inbox = adhoc.readInbox(busDir, started.runId);
    assert.strictEqual(inbox.plan.name, '設計を練る（対話）');
    assert.ok(inbox.request.includes('## 元の要望\n依頼欄に設計書を読み込めるようにする'));
    assert.strictEqual(inbox.workspace, null, '設計 run はワークスペースを取らない（cwd 未指定）');

    // run が done になったら設計書と質問を取り込む
    const runDir = path.join(busDir, 'runs', started.runId);
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({ status: 'done' }));
    fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: { draft: { id: 'draft', deps: [] } } }));
    fs.writeFileSync(path.join(runDir, 'results', 'draft.json'), JSON.stringify({
      status: 'done', output: '## 目的\nやること\n\n## 質問\n1. 対象は A と B のどちらか',
    }));
    const harvested = design.getSession(cfg, started.id);
    assert.strictEqual(harvested.runStatus, 'done');
    assert.strictEqual(harvested.document, '## 目的\nやること');
    assert.deepStrictEqual(harvested.questions, ['対象は A と B のどちらか']);
    assert.deepStrictEqual(harvested.rounds[0].questions, ['対象は A と B のどちらか']);

    // 次のラウンドは回答を運び、直前の設計書を入力に載せる
    const next = design.startRound(cfg, { id: started.id, answers: ['A'] });
    assert.strictEqual(next.rounds.length, 2);
    assert.deepStrictEqual(next.questions, [], '新しいラウンドの開始で古い質問は消える');
    const nextInbox = adhoc.readInbox(busDir, next.runId);
    assert.ok(nextInbox.request.includes('## 現在の設計書\n## 目的\nやること'));
    assert.ok(nextInbox.request.includes('1. 対象は A と B のどちらか\n   → A'));

    assert.strictEqual(design.listSessions(cfg).length, 1);
    assert.strictEqual(design.listSessions(cfg)[0].document, undefined, '一覧は本文を運ばない');
    assert.strictEqual(design.deleteSession(cfg, started.id), true);
    assert.strictEqual(design.listSessions(cfg).length, 0);
    assert.throws(() => design.startRound(cfg, { goal: '' }), /やりたいこと/);
    assert.throws(() => design.startRound(cfg, { goal: 'x', mode: 'unknown' }), /進め方が不正/);
  } finally {
    exec.shInWsl = originalExec;
    profiles.resolveTier = originalTier;
  }
});

test('設計 run が失敗したらセッションに理由を残し、回答は消さない', () => {
  const busDir = tmpdir('design-fail-bus-');
  const cfg = { adhocFlow: {
    busDir, workflowDir: tmpdir('design-fail-workflow-'),
    designSessionDir: tmpdir('design-fail-sessions-'), tuningRoot: tmpdir('design-fail-tuning-'),
  } };
  const originalExec = exec.shInWsl;
  const originalTier = profiles.resolveTier;
  exec.shInWsl = () => ({ status: 0, stdout: 'launched:1', stderr: '' });
  profiles.resolveTier = () => ({ agent_cli: 'claude', model: '' });
  try {
    const started = design.startRound(cfg, { goal: 'やりたいこと', mode: 'auto' });
    const runDir = path.join(busDir, 'runs', started.runId);
    fs.mkdirSync(runDir, { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({
      status: 'failed', failure_reason: 'エージェントが応答しませんでした',
    }));
    fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: {} }));
    const harvested = design.getSession(cfg, started.id);
    assert.strictEqual(harvested.runStatus, 'failed');
    assert.strictEqual(harvested.error, 'エージェントが応答しませんでした');
    assert.strictEqual(harvested.rounds.length, 1, 'ラウンド記録は残る');
  } finally {
    exec.shInWsl = originalExec;
    profiles.resolveTier = originalTier;
  }
});

// --- 昇格（S22）: 宛先はエンジン担当プロジェクトだけ ---------------------------

test('promote はエンジンが担当していないフォルダを拒否する（C1）', () => {
  const outside = tmpdir('adhoc-outside-');
  // engine/status.json を読まない素の cfg では担当プロジェクトは空 → どこにも投函できない
  assert.throws(
    () => adhoc.promote({}, { projectDir: outside, spec: { title: 'x' } }),
    /担当しているプロジェクトではありません/
  );
  assert.throws(() => adhoc.promote({}, { spec: { title: 'x' } }), /昇格先/);
});

console.log(`\n${passed} tests passed`);
