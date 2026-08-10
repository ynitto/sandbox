'use strict';

// adhoc-flow（S21/S22: プロジェクト非依存の flow 投入・フロービルダー・昇格）の単体テスト。
// Electron は起動しない。追加依存なしで `node test/adhoc-flow.test.js` で走る。

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

const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const exec = require('../src/features/routines/main/exec');
const tuning = require('../src/features/orchestration/main/tuning');
const profiles = require('../src/features/orchestration/main/profiles');
const actions = require('../src/features/agent-project/main/actions');
const workflowUi = require('../src/renderer/features/adhoc-flow');

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

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
  assert.strictEqual(adhoc.listWorkflows(cfg).length, 1);
  assert.deepStrictEqual(adhoc.loadWorkflow(cfg, saved.id), saved);
  assert.strictEqual(saved.nodes[1].tier, 'small');
  assert.strictEqual(saved.nodes[1].x, 320);
  assert.strictEqual(saved.version, 2);
  assert.deepStrictEqual(saved.entry, ['build']);
  assert.deepStrictEqual(saved.exit, ['verify']);
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
      name: '実装と検証',
      nodes: [
        { id: 'build', goal: '実装', tier: 'large' },
        { id: 'verify', goal: '検証', tier: 'small', deps: ['build'] },
      ],
    });
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.deepStrictEqual(plan.nodes[0].agent, { agent_cli: 'codex', model: 'gpt-5' });
    assert.deepStrictEqual(plan.nodes[1].agent, { agent_cli: 'ollama', model: 'qwen3' });
  } finally {
    profiles.resolveTier = original;
  }
});

test('実行手法はノードへスナップショットされ、そのノードの指示だけへ反映される', () => {
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
    assert.deepStrictEqual(workflow.methods, ['plan-first']);
    assert.strictEqual(workflow.nodes[0].method.id, 'test-first');
    const plan = adhoc.planFromWorkflow({}, workflow);
    assert.deepStrictEqual(plan.methods, ['plan-first']);
    assert.match(plan.nodes[0].goal, /失敗する最小テスト/);
    assert.strictEqual(plan.nodes[0].method, undefined, '実行エンジンへは通常の goal として渡す');
  } finally {
    profiles.resolveTier = original;
  }
});

test('実行手法カタログから説明つきの具体ノードを作る', () => {
  const node = workflowUi.methodNodeTemplate({
    id: 'adversarial-verify', description: '反例を探してから検証判定する', origin: 'local:test',
    fragments: [{ role: 'verify', text: '具体的な反例を探してください。' }],
  }, 1, 'small');
  assert.strictEqual(node.kind, 'verify');
  assert.strictEqual(node.label, '反例を探してから検証判定する');
  assert.strictEqual(node.method.id, 'adversarial-verify');
  assert.strictEqual(node.method.text, '具体的な反例を探してください。');
  assert.strictEqual(node.tier, 'small');
  assert.strictEqual(workflowUi.methodNodeTemplate({
    id: 'plan-first', fragments: [{ role: 'planner', text: '先に計画する' }],
  }, 1, 'small'), null, 'planner 専用手法は実行ノードに見せかけない');
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
  assert.ok(workflow.nodes[1].x > workflow.nodes[0].x, '依存方向へ自動配置する');
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

test('初期画面は保存済み・一から作る・雛形を同じカード導線にまとめる', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowLibraryHtml({
      workflows: [{ id: 'saved', name: '保存済みフロー', description: '続きから編集する' }],
      patterns: [{
        id: 'verify', label: '検証つき', description: '作業後に検証する',
        template: { nodes: [{ id: 'work', kind: 'work', deps: [] }] },
      }],
    });
    assert.match(html, /data-workflow-id="saved"/);
    assert.match(html, /id="wf-new"/);
    assert.match(html, /data-pattern-id="verify"/);
    assert.doesNotMatch(html, /この雛形から作る|wf-list/);
  } finally {
    global.esc = previousEsc;
  }
});

test('次の工程は接続元の役割に合う候補を三つまで返す', () => {
  const workflow = { nodes: [
    { id: 'draft', kind: 'generate' },
    { id: 'check', kind: 'verify' },
  ] };
  assert.deepStrictEqual(workflowUi.recommendedKinds(workflow, '__start__'), ['work', 'generate', 'classify']);
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

test('フロー全体の手法は対象と適用条件を読める形にする', () => {
  const view = workflowUi.methodPresentation({
    fragments: [{ role: 'planner', text: '最初に計画する' }, { role: 'worker', text: '小さく実装する' }],
    when: { tiers: ['large'], purposes: ['coding'] },
  });
  assert.deepStrictEqual(view.roles, ['計画', '作業']);
  assert.strictEqual(view.condition, 'tier: large · 対象: coding');
});

test('全体ルールは実行時の role と purpose が一致するノードだけへ対応づける', () => {
  const methods = [{
    id: 'quality', description: '品質ルール',
    fragments: [
      { role: 'planner', text: '先に計画する' },
      { role: 'worker', text: '小さく作る' },
      { role: 'verify', text: '反例を探す' },
    ],
  }, {
    id: 'verify-only', description: '検証専用',
    fragments: [{ role: 'verify', text: '証跡を確認する' }],
    when: { purposes: ['verify'], tiers: ['large'] },
  }];
  assert.deepStrictEqual(
    workflowUi.nodeMethodApplications(methods, ['quality', 'verify-only'], { kind: 'work' })
      .map((item) => item.id),
    ['quality']
  );
  const verify = workflowUi.nodeMethodApplications(
    methods, ['quality', 'verify-only'], { kind: 'verify' }
  );
  assert.deepStrictEqual(verify.map((item) => item.id), ['quality', 'verify-only']);
  assert.strictEqual(verify[1].conditional, true, 'tier は実行時に決まるため条件つきと示す');
});

test('ユーザー定義フローの前後に非編集のシステム工程を明示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowExecutionGuideHtml({
      nodes: [{ kind: 'work' }, { kind: 'verify' }],
    });
    assert.match(html, /計画確認/);
    assert.match(html, /計画エージェントは使用しません/);
    assert.match(html, /検証ノード 1件/);
    assert.match(html, /完了判定/);
    assert.match(html, /評価エージェントによる再計画は行いません/);
    assert.doesNotMatch(html, /data-node-id|wf-port/);
  } finally {
    global.esc = previousEsc;
  }
});

test('対象ノードに全体ルールと実行時条件をバッジ表示する', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.nodeRuleBadgesHtml([{
      id: 'quality', description: '品質ルール', when: { tiers: ['large'] },
      fragments: [{ role: 'worker', text: '小さく作る' }, { role: 'planner', text: '計画する' }],
    }], ['quality'], { kind: 'work' });
    assert.match(html, /品質ルール/);
    assert.match(html, /実行時条件/);
    assert.doesNotMatch(html, /計画する/);
  } finally {
    global.esc = previousEsc;
  }
});

test('ノード編集欄で適用される指示と条件を確認できる', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.nodeRuleDetailsHtml([{
      id: 'quality', description: '品質ルール', when: { tiers: ['large'] },
      fragments: [{ role: 'worker', text: '小さく作って確認する' }],
    }], ['quality'], { kind: 'work' });
    assert.match(html, /この工程に適用されるルール/);
    assert.match(html, /品質ルール/);
    assert.match(html, /小さく作って確認する/);
    assert.match(html, /tier: large/);
  } finally {
    global.esc = previousEsc;
  }
});

test('全体ルールを開始ノードの外で選び、適用先の有無を確認できる', () => {
  const previousEsc = global.esc;
  global.esc = (value) => String(value);
  try {
    const html = workflowUi.workflowRulesHtml([{
      id: 'worker-rule', description: '作業ルール',
      fragments: [{ role: 'worker', text: '小さく作る' }],
    }, {
      id: 'planner-rule', description: '計画ルール',
      fragments: [{ role: 'planner', text: '先に計画する' }],
    }], { methods: ['worker-rule', 'planner-rule'], nodes: [{ id: 'build', kind: 'work' }] });
    assert.match(html, /data-flow-method="worker-rule"/);
    assert.match(html, /1工程に適用/);
    assert.match(html, /このフローでは適用されません/);
  } finally {
    global.esc = previousEsc;
  }
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
    name: '固定', nodes: [{ id: 'a', goal: '実装', tier: 'large' }],
  });
  const original = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'gpt-5' });
  try {
    assert.deepStrictEqual(adhoc.snapshotSelection(cfg, null), { version: 1, type: 'auto' });
    assert.deepStrictEqual(adhoc.snapshotSelection(cfg, { type: 'pattern', id: 'map-reduce' }),
      { version: 1, type: 'pattern', pattern: 'map-reduce' });
    const custom = adhoc.snapshotSelection(cfg, { type: 'custom', id: workflow.id });
    assert.strictEqual(custom.type, 'custom');
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

test('カスタムフロー全体の手法を run 専用 tuning へ渡す', () => {
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
      nodes: [{ id: 'a', goal: '{{request}}', tier: 'small' }],
    });
    const result = adhoc.submit(cfg, {
      request: '検証する', selection: { type: 'custom', id: workflow.id },
    });
    assert.deepStrictEqual(result.methods, ['adversarial-verify']);
    assert.ok(fs.existsSync(path.join(result.tuningDir, 'tuning.json')));
  } finally {
    exec.shInWsl = originalExec;
    profiles.resolveTier = originalTier;
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
