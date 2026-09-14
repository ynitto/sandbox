'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), os = require('os'), path = require('path'), crypto = require('crypto');
const store = require('../src/main/store');
const { Publications } = require('../src/main/share/publications');
const { SessionBrowser } = require('../src/main/sessionBrowser');
function fixture(t) {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'public-sessions-'));
  t.after(() => fs.rmSync(userData, { recursive: true, force: true }));
  const pub = new Publications({ userData, node: 'alice', screen: async () => 'terminal' });
  const create = (text = '公開した結果') => {
    const s = store.createSession(userData, { repo: '/repo', cli: 'claude', model: 'm' });
    store.appendMessage(userData, s.id, { role: 'user', text: '集計', attachments: [{ path: '/private/file' }] });
    store.appendMessage(userData, s.id, { role: 'assistant', text, parts: { secret: 'internal' } });
    return s;
  };
  return { userData, pub, create };
}
test('completed sessions publish independently; only public text crosses the boundary; restart, updates and revocation', async t => {
  const { userData, pub, create } = fixture(t);
  const s = create(), privateSession = create('PRIVATE');
  const entry = pub.publish(s.id);
  assert.equal(pub.publish(s.id).id, entry.id);
  const r = await pub.read(entry.id);
  assert.equal(r.messages.length, 2);
  assert.equal(r.appId, undefined);
  assert.equal(r.messages[0].attachments, undefined);
  assert.equal(r.messages[1].parts, undefined);
  assert.equal((await pub.search({ text: 'PRIVATE' })).sessions.length, 0);
  await assert.rejects(pub.read(privateSession.id), /公開が停止/);
  const again = new Publications({ userData, node: 'alice' });
  assert.equal(again.list().length, 1);
  store.appendMessage(userData, s.id, { role: 'assistant', text: '後続ターン' });
  assert.equal((await again.read(entry.id)).messages.length, 3);
  assert.notEqual((await again.read(entry.id)).revision, r.revision);
  assert.equal((await pub.view(entry.id)).screen, 'terminal');
  const unchanged = await pub.view(entry.id, (await pub.read(entry.id)).revision);
  assert.equal(unchanged.unchanged, true); assert.equal(unchanged.messages, undefined);
  pub.stop(entry.id);
  assert.equal((await pub.search({})).sessions.length, 0);
  await assert.rejects(pub.read(entry.id), /公開が停止/);
});
test('comments persist after completion, deduplicate retries, and never become agent messages', async t => {
  const { pub, create, userData } = fixture(t); const s = create(), entry = pub.publish(s.id);
  const message = { text: 'ありがとう', who: 'bob', messageId: crypto.randomUUID() };
  pub.comment(entry.id, message); pub.comment(entry.id, message);
  assert.equal((await pub.view(entry.id)).talk.length, 1);
  assert.equal(store.readSession(userData, s.id).messages.length, 2);
  assert.equal((await new Publications({ userData, node: 'alice' }).view(entry.id)).talk[0].who, 'bob');
  assert.throws(() => pub.comment(entry.id, { ...message, text: 'x'.repeat(501) }), /500/);
  pub.stop(entry.id); assert.throws(() => pub.comment(entry.id, message), /公開が停止/);
});
test('public search applies filters, bounds sparse scans, binds cursors, and excludes deleted sessions', async t => {
  const { pub, create, userData } = fixture(t);
  const ids = [];
  for (let i = 0; i < 205; i++) ids.push(pub.publish(create(i === 0 ? 'needle' : 'other').id));
  const first = await pub.search({ text: 'needle', agent: 'claude', model: 'm', repo: '/repo' });
  assert.equal(first.sessions.length, 0); assert.ok(first.cursor);
  const second = await pub.search({ text: 'needle', agent: 'claude', model: 'm', repo: '/repo' }, first.cursor);
  assert.equal(second.sessions.length, 1); assert.equal(second.cursor, '');
  await assert.rejects(pub.search({ text: 'other' }, first.cursor), /更新/);
  assert.equal((await pub.search({ source: 'cli' })).sessions.length, 0);
  assert.equal((await pub.search({ since: Date.now() / 1000 + 10 })).sessions.length, 0);
  assert.equal((await pub.search({})).sessions.length, 50);
  store.removeSession(userData, ids[0].sessionId);
  await assert.rejects(pub.read(ids[0].id), /ENOENT/);
});
test('federated results share preview and fork paths, preserve local results on peer failure, paginate without duplicates', async t => {
  const { userData, pub, create } = fixture(t);
  const entry = pub.publish(create('共有の内容').id);
  const r = await pub.read(entry.id);
  let calls = 0;
  const share = {
    publicPeers: () => [{ node: 'alice' }, { node: 'offline' }],
    searchPublic: async node => { calls++; if (node === 'offline') throw new Error('offline'); return pub.search({}); },
    readPublic: async () => pub.read(entry.id),
  };
  const browser = new SessionBrowser({ userData: () => userData, share, getTargets: async () => [] });
  await browser.search({ source: 'app' }, 'local'); assert.equal(calls, 0);
  for (let i = 0; i < 55; i++) create();
  const query = { shared: true, source: 'app' };
  let page = await browser.search(query, 'shared'), records = [...page.sessions];
  assert.equal(page.sessions.length, 50); assert.ok(page.errors.some(e => e.message === 'offline'));
  const first = page;
  while (page.cursor) { page = await browser.search(query, crypto.randomUUID(), page.cursor); records.push(...page.sessions); }
  assert.equal(records.length, 57); assert.equal(new Set(records.map(r => r.key)).size, 57);
  assert.equal(calls, 2);
  assert.ok(records.some(r => r.owner === 'alice'));
  assert.deepEqual(await browser.search(query, 'back', page.previous), first);
  const preview = await browser.read(r.key);
  let summaryInput = '';
  const prepared = await browser.prepare({ key: r.key, revision: preview.revision, boundary: '1', repo: '/target', cli: 'codex', model: '', mode: 'fork' }, async prompt => { summaryInput = prompt; return '引き継ぎ'; });
  assert.match(summaryInput, /共有の内容/);
  const result = browser.create({ token: prepared.token, summary: prepared.summary });
  assert.equal(result.session.repo, '/target'); assert.equal(result.session.cli, 'codex');
  assert.equal(result.session.externalOrigin.key, r.key);
  store.appendMessage(userData, entry.sessionId, { role: 'assistant', text: '変更' });
  await assert.rejects(browser.prepare({ key: r.key, revision: preview.revision }, () => ''), /更新/);
  pub.stop(entry.id); await assert.rejects(browser.read(r.key), /公開が停止/);
});
test('cancelling federated search aborts peer I/O without committing results', async t => {
  const { userData } = fixture(t);
  let requested;
  const reached = new Promise(resolve => { requested = resolve; });
  const share = { publicPeers: () => [{ node: 'slow' }], searchPublic: (_node, _query, _cursor, signal) => new Promise((resolve, reject) => {
    requested(); signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) };
  const browser = new SessionBrowser({ userData: () => userData, share, getTargets: async () => [] });
  const job = browser.search({ shared: true, source: 'app' }, 'cancel');
  await reached; browser.cancel('cancel'); await assert.rejects(job, /中止/);
  assert.equal(browser.sharedSearch.jobs.size, 0);
});

test('cancelling a later remote page preserves the cursor and all unread results for retry', async t => {
  const { userData } = fixture(t);
  let calls = 0, entered;
  const waiting = new Promise(resolve => { entered = resolve; });
  const share = {
    publicPeers: () => [{ node: 'remote' }],
    searchPublic: async (_node, _query, cursor, signal) => {
      calls++;
      if (!cursor) return { sessions: Array.from({ length: 50 }, (_, i) => ({ key: `remote-${i}`, updatedAt: i })), errors: [], cursor: 'next' };
      assert.equal(cursor, 'next');
      if (calls === 2) return new Promise((_resolve, reject) => { entered(); signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }); });
      return { sessions: [{ key: 'last', updatedAt: 0 }], errors: [], cursor: '' };
    },
  };
  const browser = new SessionBrowser({ userData: () => userData, share, getTargets: async () => [] });
  const query = { shared: true, source: 'app' };
  const first = await browser.search(query, 'one');
  const second = browser.search(query, 'two', first.cursor);
  await waiting; browser.cancel('two'); await assert.rejects(second, /中止/);
  const retry = await browser.search(query, 'retry', first.cursor);
  assert.equal(retry.sessions[0].key, 'last'); assert.equal(retry.errors.length, 0);
  assert.equal(retry.total, 51); assert.equal(calls, 3);
  assert.deepEqual(await browser.search(query, 'back', retry.previous), first);
});
