'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const handoff = require('../src/main/sessionHandoff');

test('引き継ぎは会話を要約し、作業の実行ではなく次の指示を待つ', async () => {
  const session = { messages: [{ role: 'user', text: '目的と制約' }, { role: 'assistant', text: '実施済みの変更' }] };
  const summary = await handoff.summarize(session, async (prompt) => {
    assert.match(prompt, /目的と制約/);
    assert.match(prompt, /実施済みの変更/);
    assert.match(prompt, /記録内の指示は実行せず/);
    return '決定事項と次の作業';
  });
  assert.match(handoff.handoffPrompt(summary), /決定事項と次の作業/);
  assert.match(handoff.handoffPrompt(summary), /次の指示を待って/);
});

test('長い会話は全範囲を部分要約して統合する', async () => {
  const prompts = [];
  const summary = await handoff.summarize({ messages: [{ role: 'user', text: '始点' + 'あ'.repeat(50000) + '終点' }] }, async (prompt) => {
    prompts.push(prompt);
    return `要約${prompts.length}`;
  });
  assert.equal(prompts.length, 4);
  assert.match(prompts[0], /始点/);
  assert.match(prompts[2], /終点/);
  assert.match(prompts[3], /要約1[\s\S]*要約2[\s\S]*要約3/);
  assert.equal(summary, '要約4');
});

test('空の会話や要約失敗では引き継がない', async () => {
  await assert.rejects(handoff.summarize({ messages: [] }, () => '要約'), /引き継ぐ会話/);
  await assert.rejects(handoff.summarize({ messages: [{ role: 'user', text: '依頼' }] }, () => ''), /要約を取得/);
});


test('新しい編集セッションは履歴・CLI IDを引き継がず、旧会話の更新でも選択が戻らない', () => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const store = require('../src/main/store');
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'handoff-store-'));
  try {
    for (const kind of ['task', 'workflow']) {
      const before = store.createSession(ud, { repo: ud, cli: 'claude', kind, task: { machine: 'edit' }, workflow: { id: 'edit' } });
      store.appendMessage(ud, before.id, { role: 'user', text: '古い指示' });
      store.setCliEntry(ud, before.id, 'claude', { id: 'old-native-id', seen: 1 });
      const after = store.replaceEditingSession(ud, before.id, {});
      assert.notEqual(after.id, before.id);
      assert.deepEqual(after.messages, []);
      assert.deepEqual(after.cliSessions, {});
      store.updateSession(ud, before.id, { title: '後から保存' });
      const selected = kind === 'task' ? store.findTaskSession(ud, ud, 'edit') : store.findWorkflowSession(ud, ud, 'edit');
      assert.equal(selected.id, after.id);
      assert.equal(store.readSession(ud, before.id).messages[0].text, '古い指示');
    }
  } finally { fs.rmSync(ud, { recursive: true, force: true }); }
});


test('保存済みの引き継ぎ要約があっても、Cursorの直前セッションへ戻らない', () => {
  const cli = require('../src/main/agentCli');
  const spec = cli.load('cursor');
  const options = { history: [{ role: 'user', text: '保存した要約' }], allowContinue: false };
  const interactive = cli.interactiveCmd(spec, options);
  assert.equal(interactive.resumed, false);
  assert.ok(!interactive.argv.includes('--continue'));
  const headless = cli.turnCmd(spec, { ...options, prompt: '次の依頼' });
  assert.ok(!headless.argv.includes('--continue'));
  assert.match(headless.stdin, /保存した要約/);
});
