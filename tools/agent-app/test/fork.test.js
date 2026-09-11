'use strict';

// 別のリポジトリへの分岐: AI の返答から @fork 行を拾う約束事、依頼文への作法の添え方、
// 分岐した会話の保存形式（origin）と分岐元からの一覧。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const ForkProtocol = require('../src/renderer/forkProtocol');
const setup = require('../src/main/sessionSetup');
const store = require('../src/main/store');

test('返答の @fork 行と、その下の本文を分岐の依頼として拾う', () => {
  const text = [
    '共通ライブラリ側の型も直す必要があります。',
    '',
    '@fork /home/me/src/shared-lib',
    '型定義 User に role: "admin" | "member" を追加してください。',
    '',
    '- src/types/user.ts を直す',
    '- npm test を通す',
  ].join('\n');
  assert.deepStrictEqual(ForkProtocol.parseForkRequests(text), [{
    folder: '/home/me/src/shared-lib',
    prompt: '型定義 User に role: "admin" | "member" を追加してください。\n\n- src/types/user.ts を直す\n- npm test を通す',
  }]);
  assert.strictEqual(ForkProtocol.parseForkRequest(text).folder, '/home/me/src/shared-lib');
  // 引用符や箇条書きの印、コードフェンスは取り除く。複数あれば順に。フォルダの無い行は拾わない
  const many = ForkProtocol.parseForkRequests('- @fork `C:\\work\\a`\n```\n本文 A\n```\n@fork: "/b"\n本文 B\n@fork\n捨てる');
  assert.deepStrictEqual(many.map((r) => [r.folder, r.prompt]), [['C:\\work\\a', '本文 A'], ['/b', '本文 B']]);
  assert.strictEqual(ForkProtocol.parseForkRequest('ふつうの返答\n@forkable ではない'), null);
  assert.strictEqual(ForkProtocol.parseForkRequest(''), null);
});

test('分岐先の最初の依頼には元の会話の所在を添える', () => {
  const prompt = ForkProtocol.forkPrompt({ originRepo: '/home/me/src/my-app', originTitle: 'ログイン画面を直す', prompt: '型を足す' });
  assert.match(prompt, /^my-app の会話「ログイン画面を直す」からの依頼です。元の会話の作業フォルダ: \/home\/me\/src\/my-app\n\n型を足す$/);
  assert.match(ForkProtocol.forkPrompt({ prompt: '本文' }), /^別の会話からの依頼です。\n\n本文$/);
});

test('共通指示に分岐の作法を添える（会話だけ。設定で外せる）', () => {
  const instructions = { enabled: true, text: '日本語で', forkEnabled: true };
  const fork = { repos: ['/repo/a', '/repo/b'], current: '/repo/a' };
  const withFork = setup.withInstructions('依頼', instructions, { fork });
  assert.match(withFork, /@fork <フォルダの絶対パス>/);
  assert.match(withFork, /分岐先に選べるフォルダ: \/repo\/b$/m);
  assert.doesNotMatch(withFork, /分岐先に選べるフォルダ: .*\/repo\/a/);
  assert.ok(withFork.indexOf('日本語で') < withFork.indexOf('@fork'));
  // 共通指示の本文が空でも、作法だけは添える
  assert.match(setup.withInstructions('依頼', { enabled: true, text: '', forkEnabled: true }, { fork }), /## 共通指示[\s\S]*@fork/);
  // fork を渡さない（タスクの会話）・設定で外した・共通指示ごと無効、のときは添えない
  assert.strictEqual(setup.withInstructions('依頼', { enabled: true, text: '', forkEnabled: true }), '依頼');
  assert.doesNotMatch(setup.withInstructions('依頼', { ...instructions, forkEnabled: false }, { fork }), /@fork/);
  assert.strictEqual(setup.withInstructions('依頼', { ...instructions, enabled: false }, { fork }), '依頼');
});

test('分岐した会話は origin を持ち、分岐元からたどれる', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-fork-'));
  const origin = store.createSession(ud, { repo: '/repo/a', cli: 'claude', model: 'sonnet', policy: 'quality', tier: 'large', readonly: false, autoApprove: true });
  store.appendMessage(ud, origin.id, { role: 'user', text: 'ログイン画面を直す' });
  store.appendMessage(ud, origin.id, { role: 'assistant', text: '@fork /repo/b\n型を足す' });
  const fork = store.createSession(ud, {
    repo: '/repo/b', cli: origin.cli, model: origin.model, policy: origin.policy, tier: origin.tier,
    origin: { sessionId: origin.id, repo: origin.repo, index: 1 },
  });
  assert.deepStrictEqual(store.readSession(ud, fork.id).origin, { sessionId: origin.id, repo: '/repo/a', index: 1 });
  assert.strictEqual(store.readSession(ud, origin.id).origin, null);
  // 分岐先は分岐先のリポジトリの会話一覧に、ふつうの会話と同じ形で並ぶ
  assert.deepStrictEqual(store.listSessions(ud, '/repo/b').map((s) => [s.id, s.origin.sessionId]), [[fork.id, origin.id]]);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/a').map((s) => s.id), [origin.id]);
  assert.deepStrictEqual(store.listForks(ud, origin.id).map((s) => s.id), [fork.id]);
  assert.deepStrictEqual(store.listForks(ud, fork.id), []);
  // 壊れた origin は捨てる。位置が不明なら -1
  const broken = store.createSession(ud, { repo: '/repo/b', cli: 'claude', origin: { sessionId: 'nope' } });
  assert.strictEqual(store.readSession(ud, broken.id).origin, null);
  const unknown = store.createSession(ud, { repo: '/repo/b', cli: 'claude', origin: { sessionId: origin.id, index: 'x' } });
  assert.strictEqual(store.readSession(ud, unknown.id).origin.index, -1);
});
