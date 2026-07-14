'use strict';

// project.js parseTask が offloaded ステータス（非ブロッキング委譲・act_async）を正しく解釈し、
// flow_run / flow_loc を extra に保持することを検証する軽量テスト。追加依存なし。

const assert = require('assert');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('parseTask は offloaded を既知ステータスとして採用する（inbox に化けない）', () => {
  const md =
    '## T1: 委譲中のタスク\n- status: offloaded\n- source: human\n- verify: `true`\n' +
    '- retries: 0\n- flow_run: req-abc-T1-r0\n- flow_loc: daemon\n';
  const t = project.parseTask(md, 'T1');
  assert.strictEqual(t.status, 'offloaded');
  assert.strictEqual(t.extra.flow_run, 'req-abc-T1-r0'); // フロータブの run へ辿るための run-id
  assert.strictEqual(t.extra.flow_loc, 'daemon');
});

test('parseTask は未知ステータスは既定 inbox のまま（後方互換）', () => {
  const t = project.parseTask('## T2: x\n- status: bogus\n', 'T2');
  assert.strictEqual(t.status, 'inbox');
});

test('parseTask は従来ステータスもそのまま', () => {
  for (const st of ['ready', 'doing', 'blocked', 'review', 'done', 'draft', 'inbox']) {
    assert.strictEqual(project.parseTask(`## T: x\n- status: ${st}\n`, 'T').status, st);
  }
});

console.log(`\n${passed} passed`);
