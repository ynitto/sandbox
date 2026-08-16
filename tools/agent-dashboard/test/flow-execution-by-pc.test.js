'use strict';

// 「どの端末がどの工程を実行したか」の可視化（棚卸し 2026-07-27 §2b.3-2）。
//   - readRun: results/<id>.json の node（実行した端末）を工程へ写し、run 単位で集計する。
//   - 端末の記録が無い古い結果は who を見出しにして `?` を付ける（名義から端末を推測しない）。
//   - runByPcHtml: 記録のある端末だけを画面に出す（推測した見出しは出さない）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');

const renderer = require('./helpers/renderer-src').read();

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
const runByPcHtml = new Function(`${grab('esc')}; ${grab('runByPcHtml')}; return runByPcHtml;`)();

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

function makeRun(busDir, runId, nodeIds) {
  const runDir = path.join(busDir, 'runs', runId);
  const nodes = {};
  for (const id of nodeIds) nodes[id] = { goal: 'g', deps: [] };
  writeJson(path.join(runDir, 'meta.json'),
    { request: 'r', status: 'done', created_at: '2026-01-01T00:00:00Z' });
  writeJson(path.join(runDir, 'graph.json'), { nodes, iteration: 0 });
  return runDir;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-bypc-'));
const bus = path.join(tmp, 'bus');

test('結果の node が工程の実行端末になり、端末別に集計される', () => {
  const runDir = makeRun(bus, 'run-a', ['t1', 't2', 't3']);
  writeJson(path.join(runDir, 'results', 't1.json'),
    { id: 't1', who: 'desk-a-w1', node: 'desk-a', status: 'done', output: '' });
  writeJson(path.join(runDir, 'results', 't2.json'),
    { id: 't2', who: 'desk-a-w2', node: 'desk-a', status: 'done', output: '' });
  writeJson(path.join(runDir, 'results', 't3.json'),
    { id: 't3', who: 'desk-b-w1', node: 'desk-b', status: 'failed', output: '' });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.t1.pc, 'desk-a');
  assert.strictEqual(run.nodes.t3.pc, 'desk-b');
  // 多い順。失敗した工程も「その端末が実行した」ことに変わりはないので数える
  assert.deepStrictEqual(run.byPc, [
    { pc: 'desk-a', count: 2 },
    { pc: 'desk-b', count: 1 },
  ]);
});

test('端末の記録が無い結果は who? を見出しにする（名義から推測しない）', () => {
  const runDir = makeRun(bus, 'run-b', ['t1']);
  writeJson(path.join(runDir, 'results', 't1.json'),
    { id: 't1', who: 'worker-1', status: 'done', output: '' });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.t1.pc, null);
  assert.deepStrictEqual(run.byPc, [{ pc: 'worker-1?', count: 1 }]);
});

test('未実行の工程は端末別集計に入らない', () => {
  const runDir = makeRun(bus, 'run-c', ['t1']);
  const run = flow.readRun(runDir);
  assert.deepStrictEqual(run.byPc, []);
});

test('画面は記録のある端末だけを出す（推測した見出しは出さない）', () => {
  const html = runByPcHtml({ byPc: [{ pc: 'desk-a', count: 2 }, { pc: 'worker-1?', count: 1 }] });
  assert.ok(html.includes('desk-a'), '記録のある端末は出る');
  assert.ok(html.includes('2 工程'));
  assert.ok(!html.includes('worker-1'), '推測の見出しは画面に出さない');
});

test('端末の記録がまったく無い実行では端末行そのものを出さない', () => {
  assert.strictEqual(runByPcHtml({ byPc: [{ pc: 'worker-1?', count: 1 }] }), '');
  assert.strictEqual(runByPcHtml({}), '');
});

// run の完了条件（終端の検証）と公開後の CI は agent-flow が final.json へ書く。
// 画面がそこから読めるよう、バスパーサはこの 2 つを解釈せずそのまま運ぶ。
// 設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P1・P6
test('final の検証結果と CI 結果はそのまま run へ運ぶ', () => {
  const runDir = makeRun(bus, 'run-d', ['t1']);
  writeJson(path.join(runDir, 'final.json'), {
    finished_at: '2026-08-16T00:00:00Z', summary: 'まとめ',
    verification: { state: 'failed', nodes: ['t1'], failed: ['t1'] },
    ci: { state: 'failed', url: 'https://ci.example/1' },
  });
  const run = flow.readRun(runDir);
  assert.deepStrictEqual(run.final.verification, { state: 'failed', nodes: ['t1'], failed: ['t1'] });
  assert.deepStrictEqual(run.final.ci, { state: 'failed', url: 'https://ci.example/1' });
});

test('古い final には検証・CI の記録が無い（null で運ぶ）', () => {
  const runDir = makeRun(bus, 'run-e', ['t1']);
  writeJson(path.join(runDir, 'final.json'), { finished_at: 'x', summary: 'まとめ' });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.final.verification, null);
  assert.strictEqual(run.final.ci, null);
});

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`\n${passed} passed`);
