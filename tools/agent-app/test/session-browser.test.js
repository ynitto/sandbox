'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const { SessionBrowser, takeBoundary, matches } = require('../src/main/sessionBrowser');
const store = require('../src/main/store');
// 検索 1 回につき保存先ごとに 1 つ起こすワーカーの代わり。要求をそのまま受けて答える。
function fakeWorker(handler) {
  return target => ({ request: (payload, onEvent = () => {}) => handler(target, payload, onEvent), close() {}, kill() {} });
}
// 流れてくる結果を受け取り、最後の打ち止めと一緒に返す。
async function run(browser, query = {}, requestId = 'req') {
  const sessions = [], progress = [], order = [];
  const done = await browser.search(query, requestId, event => {
    if (event.hit) { sessions.push(...event.hit.sessions); order.push('hit'); }
    if (event.progress) { progress.push(event.progress); order.push('progress'); }
  });
  return { sessions, progress, order, done };
}
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'session-browser-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const repo = path.join(dir, 'repo'); fs.mkdirSync(repo);
  store.saveConfig(dir, { repos: [repo] });
  return { dir, repo };
}
test('search streams app and external sessions and prepares a fresh cross-agent fork', async t => {
  const { dir, repo } = fixture(t);
  const original = store.createSession(dir, { repo, cli: 'codex' });
  store.appendMessage(dir, original.id, { role: 'user', text: 'app request' });
  store.appendMessage(dir, original.id, { role: 'assistant', text: 'app result' });
  const external = { agent: 'copilot', provider: 'vscode', source: 'vscode', nativeId: 'vs-1', repo: '/outside', model: 'source-model', title: 'report',
    revision: 'rev1', updatedAt: Date.now() / 1000, createdAt: 1, descriptor: { path: '/store/log', provider: 'vscode' },
    messages: [{ id: '0', role: 'user', text: 'before' }, { id: '1', role: 'assistant', text: 'done', complete: true }, { id: '2', role: 'user', text: 'after' }] };
  const browser = new SessionBrowser({ userData: () => dir, index: null, getTargets: async () => [{ id: 'local' }],
    startWorker: fakeWorker(async (_target, options, onEvent) => {
      if (options.mode === 'inventory') return { descriptors: [external.descriptor], errors: [], partial: false };
      if (options.mode === 'read') return external;
      onEvent({ hit: { ...external, messages: undefined, count: 3, snippet: 'done' } });
      return { errors: [], partial: false, scanned: 1 };
    }) });
  const found = await run(browser, {});
  assert.equal(found.done.matched, 2);
  assert.equal(found.sessions.some(s => s.messages), false);
  const row = found.sessions.find(s => s.source === 'vscode');
  let input = '';
  const prepared = await browser.prepare({ key: row.key, revision: 'rev1', boundary: '1', repo, cli: 'claude', model: 'target-model', mode: 'fork' }, async prompt => { input = prompt; return '要約'; });
  assert.match(input, /before/); assert.doesNotMatch(input, /after/);
  const created = browser.create({ token: prepared.token, summary: 'edited', request: 'new direction' });
  assert.notEqual(created.session.id, original.id);
  assert.equal(created.session.cli, 'claude'); assert.equal(created.session.model, 'target-model');
  assert.equal(created.session.repo, repo); assert.deepEqual(created.session.cliSessions, {});
  assert.equal(created.session.externalOrigin.nativeId, 'vs-1');
  assert.match(created.prompt, /edited/); assert.match(created.prompt, /new direction/);
  assert.equal(browser.create({ token: prepared.token, summary: 'edited' }).session.id, created.session.id);
  assert.equal(store.readSession(dir, original.id).messages.length, 2);
  await assert.rejects(browser.prepare({ key: row.key, revision: 'stale' }, async () => 'no'), /更新/);
});
test('partial and pending conversations cannot silently become complete transfers', () => {
  assert.throws(() => takeBoundary({ partial: true }, null), /一部/);
  assert.throws(() => takeBoundary({ messages: [{ id: 'x', role: 'assistant', complete: false }] }, 'x'), /完了/);
  assert.equal(matches({ updatedAt: 20, createdAt: 10, messages: [] }, { since: 10, until: 20 }), false);
});
test('worker reads a real VS Code export through the bundled shared Python reader', async t => {
  const { dir } = fixture(t);
  const file = path.join(dir, 'export.json');
  fs.writeFileSync(file, JSON.stringify({ sessionId: 'v', requests: [{ message: { text: 'hello' }, response: [{ value: 'world' }] }] }));
  const browser = new SessionBrowser({ userData: () => dir });
  const result = await browser.once({ id: 'local', options: {} }, { mode: 'read', descriptor: { provider: 'vscode', path: file } });
  assert.equal(result.messages[1].text, 'world');
});
test('one failed source does not hide the results that could be read', async t => {
  const { dir, repo } = fixture(t);
  for (let i = 0; i < 52; i++) store.createSession(dir, { repo, cli: 'codex' });
  const browser = new SessionBrowser({ userData: () => dir, index: null, getTargets: async () => [{ id: 'broken' }],
    startWorker: fakeWorker(async () => { throw new Error('読取不可'); }) });
  const found = await run(browser, {});
  assert.equal(found.sessions.length, 52);
  assert.equal(found.done.errors[0].message, '読取不可');
});
test('Windows discovery includes the Windows profile once and every available distribution internally', async t => {
  const { dir } = fixture(t);
  const b = new SessionBrowser({ userData: () => dir, platform: 'win32', env: { USERPROFILE: 'C:\\Users\\test', APPDATA: 'C:\\Users\\test\\AppData\\Roaming' },
    execFileFn: async () => ({ stdout: Buffer.from('Ubuntu\r\nDebian\r\n', 'utf16le') }) });
  const targets = await b.targets();
  assert.deepEqual(targets.map(t => t.distro), ['Ubuntu', 'Debian']);
  assert.deepEqual(targets[0].options.extraHomes, ['/mnt/c/Users/test']);
  assert.deepEqual(targets[0].options.appData, ['/mnt/c/Users/test/AppData/Roaming']);
  assert.deepEqual(targets[1].options, {});
  b.exec = async () => { throw new Error('WSL unavailable'); };
  assert.equal((await b.targets())[0].native, true);
});
test('packaged reader runs using only the distributed Python modules', async t => {
  const { dir } = fixture(t);
  const resourcesPath = path.join(dir, 'resources');
  const to = path.join(resourcesPath, 'audit-runtime/agent_audit'); fs.mkdirSync(to, { recursive: true });
  const pkg = require('../package.json');
  const entry = pkg.build.extraResources.find(e => e.to === 'audit-runtime/agent_audit');
  for (const file of entry.filter) fs.copyFileSync(path.resolve(__dirname, '../../agent-audit/agent_audit', file), path.join(to, file));
  const input = path.join(dir, 'export.json');
  fs.writeFileSync(input, JSON.stringify({ sessionId: 'v', requests: [{ message: { text: 'input' }, response: [{ value: 'answer' }] }] }));
  const browser = new SessionBrowser({ userData: () => dir, resourcesPath });
  const result = await browser.once({ id: 'local', options: {} }, { mode: 'read', descriptor: { provider: 'vscode', path: input } });
  assert.equal(result.messages[1].text, 'answer');
});

test('results stream in newest-first order with no pages to click through', async t => {
  const { dir } = fixture(t);
  const descriptors = Array.from({ length: 125 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode', updatedAt: 1000 - i }));
  const batches = [];
  const browser = new SessionBrowser({ userData: () => dir, index: null, getTargets: async () => [{ id: 'local' }],
    startWorker: fakeWorker(async (_target, options, onEvent) => {
      if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
      batches.push(options.descriptors.length);
      for (const d of options.descriptors) onEvent({ hit: { descriptor: d, nativeId: d.path, provider: 'vscode', repo: '', updatedAt: d.updatedAt, title: d.path, snippet: '' } });
      return { errors: [], partial: false, scanned: options.descriptors.length };
    }) });
  const found = await run(browser, { source: 'vscode' });
  assert.equal(found.sessions.length, 125);
  assert.equal(found.done.matched, 125);
  assert.equal(new Set(found.sessions.map(s => s.key)).size, 125);
  const stamps = found.sessions.map(s => s.updatedAt);
  assert.deepEqual(stamps, [...stamps].sort((a, b) => b - a));
  // ワーカーへ渡すのは 1 回 200 件まで。画面は最初の一致を待たされない。
  assert.ok(batches.every(size => size <= 200), batches.join(','));
  assert.ok(found.order.indexOf('hit') < found.order.lastIndexOf('progress') + 1);
});
test('a search that matches almost nothing still scans every candidate in one go', async t => {
  const { dir } = fixture(t);
  let read = 0;
  const descriptors = Array.from({ length: 500 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode', updatedAt: 500 - i }));
  const browser = new SessionBrowser({ userData: () => dir, index: null, getTargets: async () => [{ id: 'local' }],
    startWorker: fakeWorker(async (_target, options) => {
      if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
      assert.equal(options.descriptors[0].path, `/logs/${read}`);
      read += options.descriptors.length;
      return { errors: [], partial: false, scanned: options.descriptors.length };
    }) });
  const found = await run(browser, { text: 'rare', source: 'vscode' });
  assert.equal(read, 500);
  assert.equal(found.sessions.length, 0);
  assert.equal(found.done.scanned, 500);
  assert.equal(found.done.matched, 0);
});
test('cancelling a search stops the workers and leaves the next search free to run', async t => {
  const { dir } = fixture(t);
  const descriptors = Array.from({ length: 60 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode', updatedAt: 60 - i }));
  let entered, killed = 0;
  const reached = new Promise(resolve => { entered = resolve; });
  let stall = false;
  const browser = new SessionBrowser({ userData: () => dir, index: null, getTargets: async () => [{ id: 'local' }],
    startWorker: target => ({
      request: async (options, onEvent = () => {}) => {
        if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
        if (stall) { entered(); return new Promise(() => {}); }
        for (const d of options.descriptors) onEvent({ hit: { descriptor: d, nativeId: d.path, provider: 'vscode', repo: '', updatedAt: d.updatedAt, title: d.path, snippet: '' } });
        return { errors: [], partial: false, scanned: options.descriptors.length };
      },
      close() {}, kill() { killed++; },
    }) });
  assert.equal((await run(browser, { source: 'vscode' })).sessions.length, 60);
  stall = true;
  const pending = assert.rejects(browser.search({ source: 'vscode' }, 'stalled', () => {}), /中止/);
  await reached; browser.cancel('stalled'); await pending;
  assert.ok(killed > 0);
  stall = false;
  assert.equal((await run(browser, { source: 'vscode' }, 'retry')).sessions.length, 60);
});

for (const kind of ['task', 'workflow', 'skill']) test(`import method classifies and routes ${kind} with the chosen boundary and execution`, async t => {
  const { dir, repo } = fixture(t);
  const original = store.createSession(dir, { repo, cli: 'codex' });
  for (const [role, text] of [['user', 'before'], ['assistant', 'completed'], ['user', 'excluded later']]) store.appendMessage(dir, original.id, { role, text });
  const browser = new SessionBrowser({ userData: () => dir });
  const record = await browser.read('app:' + original.id);
  const inputs = [];
  const prepared = await browser.prepare({ key: record.key, revision: record.revision, boundary: '1', mode: 'handoff', intent: 'routine', repo, cli: 'claude', model: 'chosen-model' }, async prompt => {
    inputs.push(prompt);
    return inputs.length === 1 ? 'reusable steps' : JSON.stringify({ kind, reason: 'appropriate', purpose: 'create reusable method' });
  });
  assert.doesNotMatch(inputs.join(''), /excluded later/);
  assert.equal(prepared.boundary, '1'); assert.equal(prepared.method.kind, kind);
  const result = browser.create({ token: prepared.token, summary: prepared.summary, permission: 'auto' });
  if (kind === 'skill') {
    assert.notEqual(result.session.id, original.id); assert.equal(result.session.cli, 'claude');
    assert.equal(result.session.model, 'chosen-model'); assert.equal(result.session.autoApprove, true);
    assert.match(result.prompt, /SKILL.md/);
  } else {
    assert.equal(result.session, undefined); assert.equal(result.method.kind, kind);
    assert.equal(result.repo, repo); assert.equal(result.options.cli, 'claude'); assert.equal(result.options.model, 'chosen-model');
    assert.equal(result.options.policy, 'direct'); assert.equal(result.options.autoApprove, true);
  }
});

test('a chosen method kind skips classification and overrides the AI answer', async t => {
  const { dir, repo } = fixture(t);
  const original = store.createSession(dir, { repo, cli: 'codex' });
  for (const [role, text] of [['user', 'before'], ['assistant', 'completed']]) store.appendMessage(dir, original.id, { role, text });
  const browser = new SessionBrowser({ userData: () => dir });
  const record = await browser.read('app:' + original.id);
  const inputs = [];
  const prepared = await browser.prepare({ key: record.key, revision: record.revision, boundary: '1', mode: 'handoff', intent: 'routine', kind: 'workflow', repo, cli: 'claude', model: 'm' }, async prompt => {
    inputs.push(prompt);
    return inputs.length === 1 ? 'reusable steps' : JSON.stringify({ kind: 'task', reason: 'ignored', purpose: 'create reusable method' });
  });
  assert.match(inputs[1], /「ワークフロー」と決めています/);
  assert.equal(prepared.method.kind, 'workflow');
  assert.equal(browser.create({ token: prepared.token, summary: prepared.summary, permission: 'auto' }).method.kind, 'workflow');
  await assert.rejects(browser.prepare({ key: record.key, revision: record.revision, boundary: '1', mode: 'handoff', intent: 'routine', kind: 'other', repo, cli: 'claude', model: 'm' }, async () => ''), /種類/);
});

test('local forks keep the working directory and expose current defaults; cross-repo forks use the destination root', async t => {
  const { dir, repo } = fixture(t);
  const original = store.createSession(dir, { repo, cli: 'claude', model: 'current-model', worktree: 'topic',
    branch: 'codex/topic', readonly: true, transport: 'tmux' });
  store.appendMessage(dir, original.id, { role: 'user', text: 'first' });
  store.appendMessage(dir, original.id, { role: 'assistant', text: 'done' });
  store.appendMessage(dir, original.id, { role: 'user', text: 'second' });
  store.appendMessage(dir, original.id, { role: 'assistant', text: 'latest' });
  store.setCliEntry(dir, original.id, 'claude', { id: 'old-cli-session' });
  const browser = new SessionBrowser({ userData: () => dir });
  const record = await browser.read('app:' + original.id);
  assert.equal(record.defaults.permission, 'ask'); assert.equal(record.defaults.transport, 'tmux');
  assert.equal(takeBoundary(record).boundary, '3');
  for (const target of [repo, '/other']) {
    const prepared = await browser.prepare({ key: record.key, revision: record.revision, repo: target,
      cli: record.agent, model: record.model, mode: 'fork' }, async () => 'summary');
    const result = browser.create({ token: prepared.token, summary: prepared.summary, permission: record.defaults.permission });
    assert.equal(result.session.worktree, target === repo ? 'topic' : '');
    assert.ok(result.prompt.includes(`保存先: ${target === repo ? path.join(repo, '.worktrees', 'topic') : target}`));
    assert.equal(result.session.branch, target === repo ? 'codex/topic' : '');
    assert.equal(result.session.readonly, true); assert.equal(result.session.model, 'current-model');
    assert.equal(result.session.origin.index, 3); assert.deepEqual(result.session.cliSessions, {});
  }
  store.updateSession(dir, original.id, { readonly: false, autoApprove: true });
  assert.equal((await browser.read(record.key)).defaults.permission, 'auto');
  assert.equal(store.readSession(dir, original.id).messages.length, 4);
});

// --- 索引（アプリ側の SQLite）------------------------------------------------
const { SessionIndex } = require('../src/main/sessionIndex');
function indexed(descriptor, { title = '月次の集計', body = '集計しました。稀なキーワードです', truncated = false } = {}) {
  return { path: descriptor.path, provider: descriptor.provider, descriptorId: descriptor.nativeId || '',
    nativeId: 'native-' + path.basename(descriptor.path), size: descriptor.size, mtime: descriptor.updatedAt,
    repo: '/work/report', model: 'model-a', title, createdAt: descriptor.updatedAt - 10, updatedAt: descriptor.updatedAt,
    archived: false, count: 4, partial: false, body, truncated };
}
function indexFixture(t, descriptors, options = {}) {
  const { dir } = fixture(t);
  const index = SessionIndex.open(path.join(dir, 'session-index.db'));
  const asked = [];
  const browser = new SessionBrowser({ userData: () => dir, index, getTargets: async () => [{ id: 'local' }],
    startWorker: fakeWorker(async (_target, payload, onEvent) => {
      asked.push(payload.mode);
      if (payload.mode === 'inventory') return { descriptors: descriptors(), errors: [], partial: false };
      if (payload.mode === 'index') {
        for (const descriptor of payload.descriptors) onEvent({ record: indexed(descriptor, options.record?.(descriptor) || {}) });
        return { errors: [], indexed: payload.descriptors.length };
      }
      for (const descriptor of payload.descriptors) onEvent({ hit: { descriptor, nativeId: 'native-' + path.basename(descriptor.path),
        provider: descriptor.provider, repo: '/work/report', model: 'model-a', title: '月次の集計', snippet: '稀なキーワード',
        createdAt: descriptor.updatedAt - 10, updatedAt: descriptor.updatedAt, archived: false, count: 4, partial: false } });
      return { errors: [], partial: false, scanned: payload.descriptors.length };
    }) });
  return { dir, index, browser, asked };
}
test('the index answers the next search without reading the conversations again', async t => {
  let files = Array.from({ length: 3 }, (_, i) => ({ path: `/logs/${i}.json`, provider: 'vscode', updatedAt: 500 - i, size: 100 + i }));
  const { browser, asked } = indexFixture(t, () => files);
  const first = await run(browser, { text: '稀なキーワード' });
  assert.deepEqual(asked, ['inventory', 'index']);
  assert.equal(first.sessions.length, 3);
  assert.equal(first.done.scanned, 3);
  assert.equal(first.done.indexed, false);
  // 2 回目は解析なし。全文（3 文字以上）も 2 文字の語も索引から返す。
  for (const [attempt, text] of [['again', '稀なキーワード'], ['short', '集計']]) {
    const next = await run(browser, { text }, attempt);
    assert.equal(next.sessions.length, 3, text);
    assert.equal(next.done.scanned, 0);
    assert.equal(next.done.indexed, true);
    assert.equal(next.done.pool, 3);
  }
  assert.deepEqual(asked.slice(2), ['inventory', 'inventory']);
  assert.equal((await run(browser, { text: '一致しない語' }, 'miss')).sessions.length, 0);
  assert.equal((await run(browser, { repo: '/work/report' }, 'repo')).sessions.length, 3);
  assert.equal((await run(browser, { repo: '/other' }, 'elsewhere')).sessions.length, 0);
});
test('a changed conversation is read again and a deleted one leaves the index', async t => {
  let files = Array.from({ length: 3 }, (_, i) => ({ path: `/logs/${i}.json`, provider: 'vscode', updatedAt: 500 - i, size: 100 + i }));
  const { browser, index, asked } = indexFixture(t, () => files);
  await run(browser, {});
  assert.equal(index.stats().sessions, 3);
  files = [{ ...files[0], updatedAt: 900 }, files[1]];
  asked.length = 0;
  const second = await run(browser, {}, 'changed');
  assert.deepEqual(asked, ['inventory', 'index']);
  assert.equal(second.done.scanned, 1, '変わった 1 件だけ読み直す');
  assert.equal(second.sessions.length, 2);
  assert.equal(second.sessions[0].updatedAt, 900);
  assert.equal(index.stats().sessions, 2, '消えた会話は索引からも落ちる');
});
test('conversations too long for the index are scanned again for keyword searches', async t => {
  const files = [{ path: '/logs/long.json', provider: 'vscode', updatedAt: 500, size: 100 }];
  const { browser, asked } = indexFixture(t, () => files, { record: () => ({ truncated: true }) });
  await run(browser, {});
  asked.length = 0;
  // 条件なしなら索引のまま返す。キーワードのときは本文が切れている分を走査し直す。
  assert.equal((await run(browser, {}, 'all')).sessions.length, 1);
  assert.deepEqual(asked, ['inventory']);
  const keyword = await run(browser, { text: '稀なキーワード' }, 'keyword');
  assert.deepEqual(asked.slice(1), ['inventory', 'stream']);
  assert.equal(keyword.sessions.length, 1);
  assert.equal(keyword.done.scanned, 1);
});
