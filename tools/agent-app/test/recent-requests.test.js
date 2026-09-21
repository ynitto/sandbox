'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const store = require('../src/main/store');

test('最近の依頼は種類とリポジトリを横断し、更新順で上限まで返す', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'recent-requests-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  function add(repo, kind, day, extra = {}) {
    const s = store.createSession(dir, { repo, cli: 'codex', kind,
      task: { machine: 'check' }, workflow: { id: 'release' } });
    Object.assign(s, extra, { updatedAt: `2026-09-${day}T00:00:00.000Z` });
    fs.writeFileSync(path.join(dir, 'sessions', `${s.id}.json`), JSON.stringify(s));
    return s.id;
  }
  const conversation = add('/a', 'conversation', '10');
  const task = add('/b', 'task', '12');
  const workflow = add('/a', 'workflow', '11');
  add('/removed', 'conversation', '15');
  add('/a', 'conversation', '14', { supersededBy: conversation });
  assert.deepEqual(store.recentSessions(dir, ['/a', '/b']).map(s => s.id), [task, workflow, conversation]);
  assert.deepEqual(store.recentSessions(dir, ['/a', '/b'], 2).map(s => s.kind), ['task', 'workflow']);
  assert.deepEqual(store.recentSessions(dir, []), []);
  assert.ok(store.listSessions(dir, '/a').every(s => s.kind === 'conversation'));
});
