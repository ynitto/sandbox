'use strict';

// 制御面分離（base / agent-project / kiro-loop / cowork / amigos / participation）の配線テスト。
// Electron は起動せず、feature 列挙・preload 合成・互換シムだけを検証する。

const assert = require('assert');
const path = require('path');
const fs = require('fs');

const { loadFeatures } = require('../src/features');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('features に各制御面が並ぶ', () => {
  const features = loadFeatures();
  const ids = features.map((f) => f.id);
  assert.deepStrictEqual(ids,
    ['agent-project', 'kiro-loop', 'cowork', 'amigos', 'orchestration', 'delegation', 'participation',
     'agent-audit']);
});

test('各 feature が registerIpc / preloadApi / configDefaults を持つ', () => {
  for (const f of loadFeatures()) {
    assert.strictEqual(typeof f.registerIpc, 'function', `${f.id}.registerIpc`);
    assert.strictEqual(typeof f.preloadApi, 'function', `${f.id}.preloadApi`);
    assert.ok(f.configDefaults && typeof f.configDefaults === 'object', `${f.id}.configDefaults`);
  }
});

test('agent-project の設定既定に engine / projects / agent がある', () => {
  const stack = loadFeatures().find((f) => f.id === 'agent-project');
  assert.ok(stack.configDefaults.projects);
  assert.ok(stack.configDefaults.agent);
  // 実行エンジンの在り処が設定の入口（プロジェクト一覧はここから受け取る）
  assert.strictEqual(stack.configDefaults.engine.distro, '');
  assert.strictEqual(stack.configDefaults.engine.home, '');
  // 本体を CLI で起こす経路は削除済み（W2-2）。設定にも残さない
  assert.strictEqual(stack.configDefaults.projects.command, undefined);
  assert.strictEqual(stack.configDefaults.projects.roots, undefined);
  assert.strictEqual(stack.configDefaults.projects.gitAutoPush, undefined);
  assert.strictEqual(stack.configDefaults.projects.flowLockDir, undefined);
});

test('agent-project preload に discover / flowRuns がある', () => {
  const stack = loadFeatures().find((f) => f.id === 'agent-project');
  const api = stack.preloadApi();
  assert.strictEqual(typeof api.discover, 'function');
  assert.strictEqual(typeof api.flowRuns, 'function');
  assert.strictEqual(typeof api.agentOpenChat, 'function');
  const calls = [];
  const discover = api.discover((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(discover(), 'ok');
  assert.deepStrictEqual(calls, [['dashboard:discover', undefined]]);
});

test('kiro-loop は tmux 視聴・構造化状態・復旧送信 API を登録する', () => {
  const loop = loadFeatures().find((f) => f.id === 'kiro-loop');
  const registered = [];
  loop.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(
    registered.sort(),
    ['kiroLoop:capture', 'kiroLoop:listSessions', 'kiroLoop:send', 'kiroLoop:state'].sort()
  );
  const api = loop.preloadApi();
  assert.strictEqual(typeof api.kiroLoopListSessions, 'function');
  assert.strictEqual(typeof api.kiroLoopCapture, 'function');
  assert.strictEqual(typeof api.kiroLoopState, 'function');
  assert.strictEqual(typeof api.kiroLoopSend, 'function');
  assert.ok(loop.configDefaults.kiroLoop);
});


test('cowork は定期実行と定型業務 API を登録する', () => {
  const cowork = loadFeatures().find((f) => f.id === 'cowork');
  assert.ok(cowork.configDefaults.cowork);
  assert.strictEqual(cowork.configDefaults.cowork.loopProvider, 'kiro-loop');
  assert.strictEqual(cowork.configDefaults.cowork.nextLoopProvider, 'agent-loop');
  const api = cowork.preloadApi();
  assert.strictEqual(typeof api.coworkOverview, 'function');
  assert.strictEqual(typeof api.coworkSaveWork, 'function');
  assert.strictEqual(typeof api.coworkGenerateStateMachine, 'function');
  const calls = [];
  const overview = api.coworkOverview((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(overview(), 'ok');
  assert.deepStrictEqual(calls, [['cowork:overview', {}]]);
  assert.strictEqual(overview({ probeProcess: true }), 'ok');
  assert.deepStrictEqual(calls[1], ['cowork:overview', { probeProcess: true }]);
  assert.deepStrictEqual(cowork.configDefaults.cowork.items, []);
});

test('amigos はミッションビューとノード予算 API を登録する', () => {
  const amigos = loadFeatures().find((f) => f.id === 'amigos');
  assert.ok(amigos.configDefaults.amigos);
  assert.deepStrictEqual(amigos.configDefaults.amigos.busDirs, []);
  const registered = [];
  amigos.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(),
    ['amigos:accept', 'amigos:budgetSave', 'amigos:buildTeam', 'amigos:claim',
     'amigos:deliveryContents', 'amigos:deliveryExport', 'amigos:overview', 'amigos:reject',
     'amigos:request'].sort());
  const api = amigos.preloadApi();
  assert.strictEqual(typeof api.amigosOverview, 'function');
  assert.strictEqual(typeof api.amigosBudgetSave, 'function');
  assert.strictEqual(typeof api.amigosRequest, 'function');
  assert.strictEqual(typeof api.amigosBuildTeam, 'function');
  assert.strictEqual(typeof api.amigosClaim, 'function');
  assert.strictEqual(typeof api.amigosAccept, 'function');
  assert.strictEqual(typeof api.amigosReject, 'function');
  assert.strictEqual(typeof api.amigosDeliveryContents, 'function');
  assert.strictEqual(typeof api.amigosDeliveryExport, 'function');
  const calls = [];
  const overview = api.amigosOverview((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(overview(), 'ok');
  assert.deepStrictEqual(calls, [['amigos:overview', {}]]);
});

test('互換シム src/main/project.js が実体へ届く', () => {
  const viaShim = require('../src/main/project');
  const viaReal = require('../src/features/agent-project/main/project');
  assert.strictEqual(viaShim, viaReal);
  assert.strictEqual(typeof viaShim.discover, 'function');
});

test('base / feature の入口ファイルが存在する', () => {
  const root = path.join(__dirname, '..', 'src');
  for (const rel of [
    'base/main/main.js',
    'base/main/ipc.js',
    'base/main/config.js',
    'features/index.js',
    'features/agent-project/index.js',
    'features/kiro-loop/index.js',
    'features/kiro-loop/README.md',
    'features/cowork/index.js',
    'features/cowork/README.md',
    'features/amigos/index.js',
    'features/amigos/README.md',
    'features/orchestration/index.js',
    'features/orchestration/config.js',
    'features/orchestration/preload.js',
    'features/orchestration/main/budget.js',
    'features/orchestration/main/control.js',
    'features/orchestration/main/agents.js',
    'features/orchestration/main/ipc.js',
    'features/participation/index.js',
    'features/participation/model.js',
    'features/participation/preload.js',
    'features/participation/main/participation.js',
    'features/participation/main/ipc.js',
    'features/agent-audit/index.js',
    'features/agent-audit/config.js',
    'features/agent-audit/preload.js',
    'features/agent-audit/README.md',
    'features/agent-audit/main/audit.js',
    'features/agent-audit/main/ipc.js',
  ]) {
    assert.ok(fs.existsSync(path.join(root, rel)), rel);
  }
});

test('HTML に data-feature マーカーがある', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.ok(html.includes('data-feature="agent-project"'));
  assert.ok(html.includes('data-feature="kiro-loop"'));
  assert.ok(html.includes('data-feature="cowork"'));
  assert.ok(html.includes('tab-cowork'));
  assert.ok(html.includes('data-feature="amigos"'));
  assert.ok(html.includes('tab-amigos'));
  assert.ok(html.includes('data-feature="orchestration"'));
  assert.ok(html.includes('tab-orchestration'));
  assert.ok(html.includes('data-feature="participation"'));
  assert.ok(html.includes('tab-participation'));
  // agent-audit は独立タブを持たない（全体設定「利用状況」へ差し込む面）。
  // 画面側の配線はスクリプトの読み込みだけで、data-feature マーカーは持たない。
  assert.ok(html.includes('features/agent-audit.js'));
  assert.ok(!html.includes('tab-agent-audit'));
});

test('orchestration はノード予算 v2 / 制御 / ドロップイン API を登録する', () => {
  const orch = loadFeatures().find((f) => f.id === 'orchestration');
  assert.ok(orch.configDefaults.orchestration);
  assert.strictEqual(orch.configDefaults.orchestration.refreshSec, 15);
  const registered = [];
  orch.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(),
    ['orchestration:agentDelete', 'orchestration:agentSave', 'orchestration:budgetSave',
     'orchestration:calibrate', 'orchestration:controlSave', 'orchestration:instructionsSave',
     'orchestration:lifecycle', 'orchestration:overview', 'orchestration:rebalance',
     'orchestration:sessionCommandsPreview', 'orchestration:sessionCommandsSave',
     'orchestration:skillsInventory'].sort());
  const api = orch.preloadApi();
  const calls = [];
  const overview = api.orchestrationOverview((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(overview(), 'ok');
  assert.deepStrictEqual(calls, [['orchestration:overview', {}]]);
  for (const name of ['orchestrationBudgetSave', 'orchestrationRebalance', 'orchestrationCalibrate',
    'orchestrationControlSave', 'orchestrationLifecycle', 'orchestrationAgentSave', 'orchestrationAgentDelete']) {
    assert.strictEqual(typeof api[name], 'function', name);
  }
});

test('delegation は共通封筒の投函・一覧 API を登録する', () => {
  const del = loadFeatures().find((f) => f.id === 'delegation');
  assert.ok(del.configDefaults.delegation);
  assert.deepStrictEqual(del.configDefaults.delegation.flowBusDirs, []);
  const registered = [];
  del.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(),
    ['delegation:accept', 'delegation:award', 'delegation:cancel', 'delegation:list',
     'delegation:nodeCommand', 'delegation:nodes',
     'delegation:post', 'delegation:reject'].sort());
  const api = del.preloadApi();
  for (const name of ['delegationList', 'delegationPost', 'delegationAward',
    'delegationAccept', 'delegationReject', 'delegationCancel',
    'delegationNodes', 'delegationNodeCommand']) {
    assert.strictEqual(typeof api[name], 'function', name);
  }
  const calls = [];
  const post = api.delegationPost((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(post({ workload: 'flow', goal: 'x' }), 'ok');
  assert.deepStrictEqual(calls, [['delegation:post', { workload: 'flow', goal: 'x' }]]);
});

test('participation はrun限定ワーカーの参加 API を登録する', () => {
  const participation = loadFeatures().find((f) => f.id === 'participation');
  const registered = [];
  participation.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
  });
  assert.deepStrictEqual(registered, ['participation:flowJoin']);
  const api = participation.preloadApi();
  assert.strictEqual(typeof api.participationFlowJoin, 'function');
  const calls = [];
  const join = api.participationFlowJoin((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(join({ runId: 'run-1' }), 'ok');
  assert.deepStrictEqual(calls, [['participation:flowJoin', { runId: 'run-1' }]]);
});

test('agent-audit は LLM 不使用段（収集・集計・点検）の API だけを登録する', () => {
  const auditFeature = loadFeatures().find((f) => f.id === 'agent-audit');
  assert.ok(auditFeature.configDefaults.agentAudit);
  assert.strictEqual(auditFeature.configDefaults.agentAudit.collectIntervalMin, 0);
  const registered = [];
  auditFeature.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({}),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(),
    ['agentAudit:collect', 'agentAudit:doctor', 'agentAudit:stats', 'agentAudit:usage'].sort());
  const api = auditFeature.preloadApi();
  const calls = [];
  const usage = api.agentAuditUsage((channel, args) => {
    calls.push([channel, args]);
    return 'ok';
  });
  assert.strictEqual(usage({ period: 'month', by: 'agent_cli' }), 'ok');
  assert.deepStrictEqual(calls, [['agentAudit:usage', { period: 'month', by: 'agent_cli' }]]);
  for (const name of ['agentAuditCollect', 'agentAuditUsage', 'agentAuditStats', 'agentAuditDoctor']) {
    assert.strictEqual(typeof api[name], 'function', name);
  }
});

console.log(`\n${passed} tests passed`);
