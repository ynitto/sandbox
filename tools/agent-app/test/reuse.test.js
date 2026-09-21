'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const reuse = require('../src/shared/reuse');
const routine = require('../src/main/automation/routine');
const history = require('../src/main/automation/run-history');
test('calendar defaults handle year changes and leap days without changing literals', () => {
  assert.deepEqual(reuse.resolveInputs({ month: '@date:previous-month', day: '@date:yesterday', literal: 'today' }, new Date(2026, 0, 1)), { month: '2025-12', day: '2025-12-31', literal: 'today' });
  assert.equal(reuse.resolveDate('@date:yesterday', new Date(2024, 2, 1)), '2024-02-29');
  assert.equal(reuse.resolveDate('@date:month', new Date(2026, 8, 30)), '2026-09');
  assert.equal(reuse.resolveDate('constructor'), 'constructor');
  assert.equal(reuse.resolveDate('toString'), 'toString');
});
test('routine retains corrections, validates routing and rejects incomplete AI responses', () => {
  const text = reuse.conversation([{ role: 'user', text: 'CSVを集計' }, { role: 'assistant', text: 'UTF-8で失敗' }, { role: 'user', text: 'Shift-JISに訂正' }]);
  assert.match(routine.prompt(text), /Shift-JISに訂正/);
  assert.match(routine.prompt(text), /工程が複数あるだけではワークフローにしない/);
  for (const kind of ['skill', 'task', 'workflow']) assert.equal(routine.parse(JSON.stringify({ kind, reason: '根拠', purpose: '手順' })).kind, kind);
  assert.throws(() => routine.parse('{"kind":"task","purpose":"手順"}'));
  assert.throws(() => routine.parse('{"kind":"other","reason":"根拠","purpose":"手順"}'));
  // 利用者が種類を選んだときは判定を求めず、応答の種類より選択を優先する
  assert.match(routine.prompt(text, 'workflow'), /「ワークフロー」と決めています/);
  assert.doesNotMatch(routine.prompt(text, 'workflow'), /種類を判断してください/);
  assert.equal(routine.parse(JSON.stringify({ kind: 'task', reason: '観点', purpose: '手順' }), 'workflow').kind, 'workflow');
  assert.throws(() => routine.prompt(text, 'other'));
  assert.throws(() => reuse.conversation([{ role: 'user', text: 'x'.repeat(100001) }]));
});
test('artifact links retain relative files and exclude outside paths and URLs', () => {
  assert.deepEqual(reuse.artifacts('@artifact reports/month.xlsx\n[Report](reports/month.xlsx)\n[x](../outside)\n[x](https://example.com)\n@artifact /tmp/secret\n@artifact C:\\secret'), ['reports/month.xlsx']);
});
test('presets retain only execution choices and bound saved data', () => {
  const p = reuse.presets([{ name: 'Review', policy: 'direct', readonly: true, skills: ['review'], repo: '/outside', worktree: 'other', password: 'secret' }])[0];
  assert.equal(p.readonly, true); assert.equal(p.repo, undefined); assert.equal(p.password, undefined);
  assert.equal(reuse.presets(Array(30).fill({ name: 'a' })).length, 20);
});
test('saved execution conditions survive restart and stay scoped to repository and task', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reuse-history-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  history.append(dir, '/repo-a', { runId: 'run', machine: 'report', taskId: 'machine:report', finishedAt: '2026-09-13', parameters: { month: '2026-08' } });
  const snapshot = { tasks: [{ id: 'machine:report', machine: 'report', history: [] }, { id: 'machine:other', machine: 'other', history: [] }] };
  assert.equal(history.merge(dir, '/repo-a', snapshot).tasks[0].history[0].parameters.month, '2026-08');
  assert.equal(history.merge(dir, '/repo-a', snapshot).tasks[1].history.length, 0);
  assert.equal(history.merge(dir, '/repo-b', snapshot).tasks[0].history.length, 0);
});

test('creation request binds the selected kind and destination without resuming the source', () => {
  for (const [kind, target] of [['skill', '.agents/skills/'], ['task', '.statemachine/'], ['workflow', '.agents/workflows/']]) {
    const prompt = reuse.creationPrompt({ kind, purpose: '訂正済みの内容', repo: '/target', originRepo: '/source' });
    assert.ok(prompt.includes(target));
    assert.match(prompt, /選んだ保存先: \/target/);
    assert.match(prompt, /訂正済みの内容/);
    assert.match(prompt, /この新規セッション/);
  }
  assert.throws(() => reuse.creationPrompt({ kind: 'constructor', purpose: 'x', repo: '/target' }));
  assert.throws(() => reuse.creationPrompt({ kind: 'skill', purpose: ' ', repo: '/target' }));
});


test('成果物は実在するファイルだけを返す（欠落・ディレクトリ・外部リンクは除外）', async () => {
  const fs = require('fs'), os = require('os'), path = require('path');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-exists-'));
  try {
    fs.mkdirSync(path.join(root, 'reports'));
    fs.writeFileSync(path.join(root, 'reports/result.xlsx'), 'test');
    fs.symlinkSync(os.tmpdir(), path.join(root, 'outside'));
    const found = await require('../src/main/files').existingArtifacts(root,
      ['reports/result.xlsx', 'reports/results.xlsx', 'reports', '../outside', 'outside', '/etc/passwd']);
    assert.deepEqual(found, ['reports/result.xlsx']);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
