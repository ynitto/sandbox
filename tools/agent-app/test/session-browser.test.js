'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const { SessionBrowser, takeBoundary, matches } = require('../src/main/sessionBrowser');
const store = require('../src/main/store');
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'session-browser-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const repo = path.join(dir, 'repo'); fs.mkdirSync(repo);
  store.saveConfig(dir, { repos: [repo] });
  return { dir, repo };
}
test('search combines app and external sessions, paginates and prepares a fresh cross-agent fork', async t => {
  const { dir, repo } = fixture(t);
  const original = store.createSession(dir, { repo, cli: 'codex' });
  store.appendMessage(dir, original.id, { role: 'user', text: 'app request' });
  store.appendMessage(dir, original.id, { role: 'assistant', text: 'app result' });
  const external = { agent: 'copilot', provider: 'vscode', source: 'vscode', nativeId: 'vs-1', repo: '/outside', model: 'source-model', title: 'report',
    revision: 'rev1', updatedAt: Date.now() / 1000, createdAt: 1, descriptor: { path: '/store/log', provider: 'vscode' },
    messages: [{ id: '0', role: 'user', text: 'before' }, { id: '1', role: 'assistant', text: 'done', complete: true }, { id: '2', role: 'user', text: 'after' }] };
  const browser = new SessionBrowser({ userData: () => dir, getTargets: async () => [{ id: 'local' }], runWorker: async (_target, options) => options.mode === 'inventory' ? { descriptors: [external.descriptor], errors: [], partial: false } : options.mode === 'read' ? external : { sessions: [{ ...external, messages: undefined }], errors: [], partial: false } });
  const found = await browser.search({});
  assert.equal(found.total, 2);
  assert.equal(found.sessions.some(s => s.messages), false);
  const row = found.sessions.find(s => s.source === 'vscode');
  let input = '';
  const prepared = await browser.prepare({ key: row.key, revision: row.revision, boundary: '1', repo, cli: 'claude', model: 'target-model', mode: 'fork' }, async prompt => { input = prompt; return '要約'; });
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
  const result = await browser.worker({ id: 'local', options: {} }, { mode: 'read', descriptor: { provider: 'vscode', path: file } }, { children: new Set(), cancelled: false });
  assert.equal(result.messages[1].text, 'world');
});
test('pagination is bound to the search filters and one failed source preserves other results', async t => {
  const { dir, repo } = fixture(t);
  for (let i = 0; i < 52; i++) store.createSession(dir, { repo, cli: 'codex' });
  const browser = new SessionBrowser({ userData: () => dir, getTargets: async () => [{ id: 'broken' }], runWorker: async () => { throw new Error('読取不可'); } });
  const first = await browser.search({});
  assert.equal(first.sessions.length, 50); assert.ok(first.cursor); assert.equal(first.errors[0].message, '読取不可');
  const next = await browser.search({}, '', first.cursor); assert.equal(next.sessions.length, 2);
  const back = await browser.search({}, '', next.previous); assert.deepEqual(back, first);
  await assert.rejects(browser.search({ agent: 'claude' }, '', first.cursor), /更新/);
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
  const result = await browser.worker({ id: 'local', options: {} }, { mode: 'read', descriptor: { provider: 'vscode', path: input } }, { children: new Set(), cancelled: false });
  assert.equal(result.messages[1].text, 'answer');
});

test('search reads only the requested page and reuses previous pages without reading bodies again', async t => {
  const { dir } = fixture(t);
  const descriptors = Array.from({ length: 125 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode', updatedAt: 1000 - i }));
  const batches = [];
  const browser = new SessionBrowser({ userData: () => dir, getTargets: async () => [{ id: 'local' }], runWorker: async (_target, options) => {
    if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
    batches.push(options.descriptors);
    return { sessions: options.descriptors.map(d => ({ descriptor: d, nativeId: d.path, agent: 'copilot', source: 'vscode', repo: '', updatedAt: d.updatedAt, title: d.path })), errors: [], partial: false };
  } });
  const first = await browser.search({ source: 'vscode' });
  assert.equal(first.sessions.length, 50); assert.equal(first.totalExact, false);
  assert.deepEqual(batches.map(b => b.length), [50]);
  const second = await browser.search({ source: 'vscode' }, '', first.cursor);
  assert.equal(second.sessions.length, 50); assert.equal(second.page, 2);
  assert.equal(new Set([...first.sessions, ...second.sessions].map(s => s.key)).size, 100);
  assert.deepEqual(await browser.search({ source: 'vscode' }, '', second.previous), first);
  assert.deepEqual(batches.map(b => b.length), [50, 50]);
  const third = await browser.search({ source: 'vscode' }, '', second.cursor);
  assert.equal(third.sessions.length, 25); assert.equal(third.total, 125); assert.equal(third.totalExact, true); assert.equal(third.cursor, '');
});
test('sparse searches stop after a bounded batch and continue from unread candidates', async t => {
  const { dir } = fixture(t);
  let read = 0;
  const descriptors = Array.from({ length: 500 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode' }));
  const browser = new SessionBrowser({ userData: () => dir, getTargets: async () => [{ id: 'local' }], runWorker: async (_target, options) => {
    if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
    assert.equal(options.descriptors[0].path, `/logs/${read}`);
    read += options.descriptors.length;
    return { sessions: [], errors: [], partial: false };
  } });
  const first = await browser.search({ text: 'rare', source: 'vscode' });
  assert.equal(read, 200); assert.equal(first.sessions.length, 0); assert.ok(first.cursor);
  await browser.search({ text: 'rare', source: 'vscode' }, '', first.cursor);
  assert.equal(read, 400);
});

test('cancelling a later page does not consume its unread candidates', async t => {
  const { dir } = fixture(t);
  const descriptors = Array.from({ length: 60 }, (_, i) => ({ path: `/logs/${i}`, provider: 'vscode' }));
  let cancelPage = false;
  const browser = new SessionBrowser({ userData: () => dir, getTargets: async () => [{ id: 'local' }], runWorker: async (_target, options, job) => {
    if (options.mode === 'inventory') return { descriptors, errors: [], partial: false };
    if (cancelPage) { job.cancelled = true; throw new Error('cancelled'); }
    return { sessions: options.descriptors.map(d => ({ descriptor: d, nativeId: d.path, agent: 'copilot', source: 'vscode', repo: '', title: d.path })), errors: [], partial: false };
  } });
  const first = await browser.search({ source: 'vscode' });
  cancelPage = true;
  await assert.rejects(browser.search({ source: 'vscode' }, '', first.cursor), /中止/);
  cancelPage = false;
  const second = await browser.search({ source: 'vscode' }, '', first.cursor);
  assert.equal(second.sessions.length, 10); assert.equal(second.total, 60);
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
