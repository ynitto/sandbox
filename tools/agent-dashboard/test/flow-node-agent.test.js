'use strict';

// ノード詳細の「実行エージェント」表示（柱2 / C4）。
//   - readRun: results/<id>.json の agent_cli / model（書き手は agent-flow）を工程へ写す。
//   - readNodeEvents: claimed イベントの agent_cli / model を残す（実行中ノードの唯一の記録）。
//   - nodeAgentInfo: result（確定）→ 直近 claimed イベント（実行中）の順で拾い、
//     どちらにも無い旧 run・stub/gitlab executor の run では null（設定から推測しない）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');

const renderer = require('./helpers/renderer-src').read();

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
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
const makeNodeAgentInfo = new Function(
  'state', `${grab('nodeTimeline')}; ${grab('nodeAgentInfo')}; return nodeAgentInfo;`);

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj, null, 2), 'utf8');
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-node-agent-'));

function makeRun(runId, nodeIds) {
  const runDir = path.join(tmp, 'bus', 'runs', runId);
  const nodes = {};
  for (const id of nodeIds) nodes[id] = { goal: 'g', deps: [] };
  writeJson(path.join(runDir, 'meta.json'),
    { request: 'r', status: 'running', created_at: '2026-01-01T00:00:00Z' });
  writeJson(path.join(runDir, 'graph.json'), { nodes, iteration: 0 });
  return runDir;
}

test('readRun は結果の agent_cli / model を工程へ写す（無ければ null）', () => {
  const runDir = makeRun('run-a', ['t1', 't2']);
  writeJson(path.join(runDir, 'results', 't1.json'),
    { id: 't1', who: 'w1', status: 'done', output: '', agent_cli: 'claude', model: 'opus' });
  writeJson(path.join(runDir, 'results', 't2.json'),
    { id: 't2', who: 'w1', status: 'done', output: '' });   // 旧 run / stub は記録なし
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.t1.agentCli, 'claude');
  assert.strictEqual(run.nodes.t1.agentModel, 'opus');
  assert.strictEqual(run.nodes.t2.agentCli, null);
  assert.strictEqual(run.nodes.t2.agentModel, null);
});

test('readRun は明示 phase を公開し、旧runの全工程完了は finalizing へ縮退する', () => {
  const explicitDir = makeRun('run-phase-explicit', ['t1']);
  writeJson(path.join(explicitDir, 'meta.json'), {
    request: 'r', status: 'running', phase: 'verifying',
    phase_started_at: '2026-01-01T00:02:00Z', created_at: '2026-01-01T00:00:00Z',
  });
  const explicit = flow.readRun(explicitDir);
  assert.strictEqual(explicit.phase, 'verifying');
  assert.strictEqual(explicit.phaseStartedAt, '2026-01-01T00:02:00Z');

  const legacyDir = makeRun('run-phase-legacy', ['t1']);
  writeJson(path.join(legacyDir, 'results', 't1.json'),
    { id: 't1', who: 'w1', status: 'done', output: 'ok' });
  assert.strictEqual(flow.readRun(legacyDir).phase, 'finalizing');
});

test('readNodeEvents は claimed イベントの agent_cli / model を残す', () => {
  const runDir = makeRun('run-b', ['t1']);
  fs.mkdirSync(path.join(runDir, 'events'), { recursive: true });
  fs.writeFileSync(path.join(runDir, 'events', 'w1.jsonl'),
    JSON.stringify({ ts: '2026-01-01T00:01:00Z', who: 'w1', kind: 'claimed',
      node: 't1', agent_cli: 'kiro', model: null }) + '\n', 'utf8');
  const byNode = flow.readNodeEvents(runDir);
  assert.strictEqual(byNode.t1[0].agentCli, 'kiro');
  assert.strictEqual(byNode.t1[0].agentModel, null);
});

test('nodeAgentInfo は result を優先し、実行中は claimed イベントへ落ちる', () => {
  const state = {
    flowRun: {
      nodeEvents: {
        t1: [{ ts: '2026-01-01T00:01:00Z', kind: 'claimed', who: 'w1',
               agentCli: 'kiro', agentModel: null }],
      },
    },
  };
  const nodeAgentInfo = makeNodeAgentInfo(state);
  // 確定済み: result の記録が正
  assert.deepStrictEqual(
    nodeAgentInfo({ id: 't1', agentCli: 'claude', agentModel: 'opus' }),
    { cli: 'claude', model: 'opus' });
  // 実行中（result 未確定）: claimed イベントの記録
  assert.deepStrictEqual(nodeAgentInfo({ id: 't1', agentCli: null }),
    { cli: 'kiro', model: null });
  // どちらにも無い: null（設定から推測しない）
  assert.strictEqual(makeNodeAgentInfo({ flowRun: { nodeEvents: {} } })({ id: 't9' }), null);
});

console.log(`passed ${passed}`);
