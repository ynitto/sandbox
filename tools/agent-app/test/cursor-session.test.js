'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const cliSession = require('../src/main/cliSession');
const agentCli = require('../src/main/agentCli');
const store = require('../src/main/store');

test('並行するCursor会話にそれぞれIDを発行し、保存したIDで対話・ヘッドレスとも再開する', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cursor-sessions-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const shell = { run: async (script) => { calls.push(script); return { ok: true, output: `${crypto.randomUUID()}\n` }; } };
  const spec = agentCli.load('cursor');
  const sessions = [store.createSession(root, { repo: root, cli: 'cursor' }), store.createSession(root, { repo: root, cli: 'cursor' })];
  const prepared = await Promise.all(sessions.map((session) => cliSession.prepare({
    shell, cli: 'cursor', id: session.id, cwd: root, argv: agentCli.interactiveCmd(spec).argv, env: { CURSOR_CONFIG_DIR: '/custom' },
  })));
  assert.notEqual(prepared[0].sessionId, prepared[1].sessionId);
  for (let i = 0; i < sessions.length; i++) {
    store.setCliEntry(root, sessions[i].id, 'cursor', { id: prepared[i].sessionId, seen: 0 });
    const saved = store.cliEntry(store.readSession(root, sessions[i].id), 'cursor');
    const interactive = agentCli.interactiveCmd(spec, { cliSession: saved.id });
    const headless = agentCli.turnCmd(spec, { cliSession: saved.id, prompt: 'continue' });
    for (const argv of [prepared[i].argv, interactive.argv, headless.argv]) {
      assert.equal(argv[argv.indexOf('--resume') + 1], prepared[i].sessionId);
      assert.ok(!argv.includes('--continue'));
    }
    const resumed = await cliSession.prepare({ shell, cli: 'cursor', cwd: root, argv: interactive.argv });
    assert.deepEqual(resumed.argv, interactive.argv);
  }
  assert.equal(calls.length, 2, '再開時は新しいIDを発行しない');
  assert.ok(calls.every((script) => script.includes("'create-chat'") && script.includes('CURSOR_CONFIG_DIR=/custom')));
  assert.equal(agentCli.continuesLatest(spec), false);
});

test('IDがない旧Cursor会話とID作成失敗では、直前の会話を拾わず保存履歴を再送する', async () => {
  const spec = agentCli.load('cursor');
  const history = [{ role: 'user', text: 'この会話だけの履歴' }];
  const interactive = agentCli.interactiveCmd(spec, { history });
  assert.equal(interactive.resumed, false);
  assert.ok(!interactive.argv.includes('--continue'));
  assert.ok(!interactive.warning.includes('混線'));
  for (const result of [{ ok: false, output: '' }, { ok: true, output: 'invalid id' }]) {
    await assert.rejects(() => cliSession.prepare({ cli: 'cursor', argv: interactive.argv, cwd: '/repo',
      shell: { run: async () => result },
    }), /直前のセッションは使わず/);
  }
  const headless = agentCli.turnCmd(spec, { prompt: '続き', history });
  assert.ok(!headless.argv.includes('--continue'));
  assert.match(headless.stdin, /この会話だけの履歴/);
});
