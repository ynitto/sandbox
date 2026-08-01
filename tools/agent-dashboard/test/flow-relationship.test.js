'use strict';

// flow.js の run-id 解析（parseRunId）と readRun の関係性フィールドを検証する軽量テスト。
// 追加依存なしで `node test/flow-relationship.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');
const renderer = require('./helpers/renderer-src').read();

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let depth = 0;
  for (let i = renderer.indexOf('{', at); i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}' && --depth === 0) return renderer.slice(at, i + 1);
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

// eslint-disable-next-line no-new-func
const flowGraphLayout = new Function(`${grab('flowGraphLayout')}; return flowGraphLayout;`)();
// eslint-disable-next-line no-new-func
const planTimelineEntries = new Function(`${grab('planTimelineEntries')}; return planTimelineEntries;`)();
// eslint-disable-next-line no-new-func
const planFlowPhases = new Function(`${grab('planFlowPhases')}; return planFlowPhases;`)();

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- parseRunId ---
test('parseRunId は req- 形式を taskId / retries / lineage に分解する', () => {
  const p = flow.parseRunId('req-a1b2c3d4-TASK-12-r2');
  assert.strictEqual(p.taskId, 'TASK-12');
  assert.strictEqual(p.retries, 2);
  assert.strictEqual(p.rev, null);
  assert.strictEqual(p.lineageId, 'req-a1b2c3d4-TASK-12');
});

test('parseRunId は revise 世代（-v）も拾う', () => {
  const p = flow.parseRunId('req-a1b2c3d4-TASK-12-r0-v3');
  assert.strictEqual(p.taskId, 'TASK-12');
  assert.strictEqual(p.retries, 0);
  assert.strictEqual(p.rev, '3');
  assert.strictEqual(p.lineageId, 'req-a1b2c3d4-TASK-12'); // 系統は同じ（リトライ/リバイズを束ねる）
});

test('parseRunId は素の run-（手動/単発）を taskId 無しにする', () => {
  const p = flow.parseRunId('run-20260705-134501-8213');
  assert.strictEqual(p.taskId, null);
  assert.strictEqual(p.lineageId, null);
});

test('同一タスクの r0 と r1 は同じ lineageId になる', () => {
  const a = flow.parseRunId('req-ff00ff00-fix.bug-r0');
  const b = flow.parseRunId('req-ff00ff00-fix.bug-r1');
  assert.strictEqual(a.lineageId, b.lineageId);
  assert.notStrictEqual(a.retries, b.retries);
});

// --- readRun が関係性フィールドを surface する ---
test('readRun は run-id 由来の taskId/lineageId と meta.inherited_from を返す', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-flow-'));
  try {
    const runId = 'req-a1b2c3d4-TASK-9-r1';
    const runDir = path.join(tmp, 'runs', runId);
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(
      path.join(runDir, 'meta.json'),
      JSON.stringify({ status: 'running', request: 'do it', inherited_from: 'req-a1b2c3d4-TASK-9-r0' })
    );
    fs.writeFileSync(
      path.join(runDir, 'graph.json'),
      JSON.stringify({ nodes: { t1: { goal: 'g', deps: [] } } })
    );
    const run = flow.readRun(runDir);
    assert.strictEqual(run.taskId, 'TASK-9');
    assert.strictEqual(run.retries, 1);
    assert.strictEqual(run.lineageId, 'req-a1b2c3d4-TASK-9');
    assert.strictEqual(run.inheritedFrom, 'req-a1b2c3d4-TASK-9-r0');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('readRun は旧形式 run-id でも作業ブランチから taskId を補う', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-rel-'));
  try {
    const runId = 'run-20260712-213419-5922';
    const runDir = path.join(tmp, 'runs', runId);
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(
      path.join(runDir, 'meta.json'),
      JSON.stringify({
        status: 'failed',
        request: 'do it',
        workspace: { branch: 'ap/TASK-9' },
      })
    );
    fs.writeFileSync(
      path.join(runDir, 'graph.json'),
      JSON.stringify({ nodes: { t1: { goal: 'g', deps: [] } } })
    );
    const run = flow.readRun(runDir);
    assert.strictEqual(run.taskId, 'TASK-9');
    assert.strictEqual(run.lineageId, null);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('計画変更イベントを新旧同じリビジョン契約へ正規化する', () => {
  const revisions = flow.normalizePlanRevisions([
    { ts: '2026-08-01T00:00:00Z', kind: 'planned', tasks: ['synth'] },
    { ts: '2026-08-01T00:01:00Z', kind: 'replan', added: ['t11'] },
    {
      ts: '2026-08-01T00:02:00Z', kind: 'inflight_amend', reason: '人の指摘を反映',
      changes: { added: [], replaced: [{ old: 't11', next: 't11-r1' }], updated: ['gate'], removed: ['t11'] },
    },
  ], ['synth', 't11-r1', 'gate']);
  assert.deepStrictEqual(revisions, [
    { revision: 0, timestamp: '2026-08-01T00:00:00Z', reason: '初期計画', added: ['synth'], replaced: [], updated: [], removed: [] },
    { revision: 1, timestamp: '2026-08-01T00:01:00Z', reason: '実行結果を受けて工程構成を見直しました', added: ['t11'], replaced: [], updated: [], removed: [] },
    { revision: 2, timestamp: '2026-08-01T00:02:00Z', reason: '人の指摘を反映', added: [], replaced: [{ old: 't11', next: 't11-r1' }], updated: ['gate'], removed: ['t11'] },
  ]);
});

test('readRun は実行イベントから計画リビジョンを返す', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-revision-'));
  try {
    const runDir = path.join(tmp, 'runs', 'run-revision');
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.mkdirSync(path.join(runDir, 'events'), { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({ status: 'running' }));
    fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({ nodes: {
      synth: { goal: '統合', deps: [] },
      t11: { goal: '追加修正', deps: [] },
    } }));
    fs.writeFileSync(path.join(runDir, 'events', 'orchestrator.jsonl'), [
      JSON.stringify({ ts: '2026-08-01T00:00:00Z', kind: 'planned', tasks: ['synth'] }),
      JSON.stringify({ ts: '2026-08-01T00:01:00Z', kind: 'replan', reason: '未解決を修正', added: ['t11'] }),
    ].join('\n'));
    const run = flow.readRun(runDir);
    assert.strictEqual(run.planRevisions.length, 2);
    assert.deepStrictEqual(run.planRevisions[1].added, ['t11']);
    assert.strictEqual(run.planRevisions[1].reason, '未解決を修正');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('計画の変遷はリビジョン順を保ち、変更された現存工程だけを返す', () => {
  const run = {
    planRevisions: [
      { revision: 0, added: ['synth'] },
      { revision: 1, added: ['t11'], updated: ['gate3'], replaced: [{ old: 'old', next: 't11' }] },
    ],
    nodes: {
      synth: { id: 'synth', deps: [] },
      t11: { id: 't11', deps: [] },
      gate3: { id: 'gate3', deps: ['t11'] },
    },
  };
  const entries = planTimelineEntries(run);
  assert.deepStrictEqual(entries.map((entry) => entry.revision.revision), [0, 1]);
  assert.deepStrictEqual(entries[0].nodeIds, []);
  assert.deepStrictEqual(entries[1].nodeIds, ['t11', 'gate3']);
});

test('各計画フェーズ内では deps だけで横位置を決める', () => {
  const layout = flowGraphLayout({ nodes: {
    synth: { id: 'synth', deps: [] },
    t11: { id: 't11', deps: [] },
    gate3: { id: 'gate3', deps: ['t11'] },
  } });
  assert.strictEqual(layout.pos.synth.x, layout.pos.t11.x);
  assert.ok(layout.pos.t11.x < layout.pos.gate3.x);
});

test('タスクの流れは初期計画と再計画の追加工程を別フェーズに分ける', () => {
  const run = {
    planRevisions: [
      { revision: 0, added: ['synth', 'gate'] },
      { revision: 1, added: ['t11'], updated: ['gate'], replaced: [] },
      { revision: 2, added: [], replaced: [{ old: 't11', next: 't11-r1' }] },
    ],
    nodes: {
      synth: { id: 'synth', deps: [] },
      gate: { id: 'gate', deps: ['synth'] },
      t11: { id: 't11', deps: [] },
      't11-r1': { id: 't11-r1', deps: ['gate'] },
    },
  };
  const phases = planFlowPhases(run);
  assert.deepStrictEqual(phases.map((phase) => phase.nodeIds), [
    ['synth', 'gate'],
    ['t11'],
    ['t11-r1'],
  ]);
  assert.deepStrictEqual(phases[2].inheritedEdges, [{ from: 'gate', to: 't11-r1' }]);
});

test('再計画があるrunだけ計画の変遷とタスクの流れを切り替える', () => {
  assert.match(renderer, /hasPlanChanges = \(run\.planRevisions \|\| \[\]\)\.length > 1/);
  assert.match(renderer, /data-flow-graph-mode="plan"/);
  assert.match(renderer, /data-flow-graph-mode="dependencies"/);
  assert.match(renderer, />タスクの流れ<\/button>/);
  assert.match(renderer, /prevTaskGraphX[\s\S]*item\.scrollLeft = prevTaskGraphX\[index\]/,
    'データ更新後も各フェーズの横スクロール位置を復元する');
});

// --- レビュー待ちイシュー ↔ run/ノードの token 対応付け ---
// gitlab.listProjectIssues がイシュー本文から抜き出す task-token（正規表現）と、
// flow.nodeTaskToken が各ノードに付ける決定的トークンが一致することを検証する。
// この一致がレビュー待ち画面の「関連 run」列の根拠になる。
const ISSUE_TOKEN_RE = /agent-flow:task-token:(kf-[0-9a-f]+)/; // gitlab.js と同一
test('イシュー本文の task-token は flow.nodeTaskToken と往復一致する', () => {
  const runId = 'req-a1b2c3d4-TASK-9-r1';
  const nodeId = 'review-1';
  const token = flow.nodeTaskToken(runId, nodeId);
  const description = `対応内容...\n\n<!-- agent-flow:task-token:${token} -->\n`;
  const m = description.match(ISSUE_TOKEN_RE);
  assert.ok(m, 'イシュー本文からトークンを抽出できる');
  assert.strictEqual(m[1], token);
  // トークンは run/ノードごとに決定的（別ノードでは異なる）
  assert.notStrictEqual(token, flow.nodeTaskToken(runId, 'review-2'));
});

test('nodeTaskToken は kf- + 12 桁 hex（抽出正規表現に合致する形）', () => {
  const token = flow.nodeTaskToken('run-x', 'n1');
  assert.match(token, /^kf-[0-9a-f]{12}$/);
});

test('nodeTaskToken は -rN / -vM 接尾辞を落として安定化する（gitlab executor と同契約）', () => {
  const a = flow.nodeTaskToken('req-ab12-T1-r0', 'n1');
  const b = flow.nodeTaskToken('req-ab12-T1-r3', 'n1');
  const c = flow.nodeTaskToken('req-ab12-T1-r3-v2', 'n1');
  assert.strictEqual(a, b, 'リトライ世代でも同一トークン');
  assert.strictEqual(a, c, 'リバイズ世代でも同一トークン');
  assert.notStrictEqual(a, flow.nodeTaskToken('req-cd34-T2-r0', 'n1'));
});

console.log(`\n${passed} passed`);
