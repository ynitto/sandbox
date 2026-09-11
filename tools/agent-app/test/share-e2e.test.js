'use strict';

// 共有の往復を、同じプロセスの中で agent-app 2〜3 台ぶん（Share）を loopback に立てて確かめる。
// UDP は使わず、静的な仲間（127.0.0.1:port）と TCP の /hello で見つけ合う。CLI は偽物（runPrompt を差し替える）。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const store = require('../src/main/store');
const settings = require('../src/main/settings');
const { Share } = require('../src/main/share');
const { Requester } = require('../src/main/share/requester');
const { call } = require('../src/main/share/server');
const { keyOf } = require('../src/main/share/peers');

const PASS = 'team-secret';

function tmp(name) { return fs.mkdtempSync(path.join(os.tmpdir(), `share-${name}-`)); }

function waitFor(fn, { timeoutMs = 8000, everyMs = 50 } = {}) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const loop = () => {
      let v;
      try { v = fn(); } catch (err) { reject(err); return; }
      if (v) { resolve(v); return; }
      if (Date.now() - started > timeoutMs) { reject(new Error('待ちきれない')); return; }
      setTimeout(loop, everyMs);
    };
    loop();
  });
}

// 偽の CLI。answers: 呼ばれるたびに返す結果（{ text } か { errorClass }）。stop で止められる
function fakeRunner(answers, { delayMs = 30 } = {}) {
  const calls = [];
  const runner = ({ cli, prompt, cwd, files }) => {
    calls.push({ cli, prompt, cwd, files });
    const script = answers.length > 1 ? answers.shift() : answers[0];
    let stopped = false;
    let timer = null;
    let finish = null;
    const done = new Promise((resolve) => {
      finish = resolve;
      if (script.hang) return;
      timer = setTimeout(() => resolve({ text: script.text || '', code: script.text ? 0 : 1, stopped: false, error: script.error || '', errorClass: script.errorClass || '', quotaKind: script.quotaKind || '', elapsedMs: delayMs, usage: null }), delayMs);
    });
    return { done, stop(reason) { stopped = true; if (timer) clearTimeout(timer); finish({ text: '', code: null, stopped: true, error: reason, errorClass: 'transient', elapsedMs: 0, usage: null }); }, get stopped() { return stopped; } };
  };
  runner.calls = calls;
  return runner;
}

async function node(name, { participate = false, clis = [], runPrompt = null, seeds = [], events = null, perRequesterDailyCap = 0 } = {}) {
  const userData = tmp(name);
  const config = settings.normalize({ share: { enabled: true, node: name, passphrase: PASS, participate, clis, peers: seeds, perRequesterDailyCap } });
  const share = new Share({
    userData, config,
    send: (channel, payload) => { if (events) events.push({ channel, payload }); },
    runPrompt: runPrompt || fakeRunner([{ text: 'ANSWER' }]),
    agents: () => clis,
    options: {
      udp: false, port: 0, host: '127.0.0.1',
      peers: { helloMs: 150, staleMs: 3000 },
      participant: { tickMs: 80, heartbeatMs: 120, outboxRetryMs: 200 },
      requester: { tickMs: 80, watchdogMs: 500 },
    },
  });
  await share.start();
  assert.equal(share.state, 'on', share.error);
  return { share, userData, config };
}

async function withNodes(t, fn) {
  const opened = [];
  t.after(async () => { for (const n of opened) await n.share.stop(); });
  const open = async (...args) => { const n = await node(...args); opened.push(n); return n; };
  return fn(open);
}

test('往復: 投函 → 仲間が拾う → 偽の CLI が答える → 依頼者の会話に assistant として戻る', async (t) => {
  await withNodes(t, async (open) => {
    const events = [];
    const a = await open('a', { events });
    const runner = fakeRunner([{ text: 'ANSWER from b' }]);
    const b = await open('b', { participate: true, clis: ['fake'], runPrompt: runner, seeds: [`127.0.0.1:${a.share.port}`] });
    await waitFor(() => a.share.peers.peers().some((p) => p.node === 'b') && b.share.peers.peers().some((p) => p.node === 'a'));
    const sess = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    let released = 0;
    const request = a.share.post({ sessionId: sess.id, title: '質問', goal: 'これは何？', requires: { agent_cli: [] }, mode: 'read' }, { onDone: () => { released += 1; } });
    assert.equal(request.state, 'open');
    assert.deepEqual(a.share.pendingSessionIds(), [sess.id]);
    const saved = await waitFor(() => { const s = store.readSession(a.userData, sess.id); return s.messages.some((m) => m.role === 'assistant') ? s : null; });
    const answer = saved.messages.find((m) => m.role === 'assistant');
    assert.equal(answer.text, 'ANSWER from b');
    assert.equal(answer.policy, 'shared');
    assert.equal(answer.share.node, 'b');
    assert.equal(answer.share.cli, 'fake');
    assert.equal(saved.share, null, '待っている印は消える');
    assert.equal(released, 1, 'turnGate の解放は 1 回');
    assert.equal(runner.calls[0].prompt, 'これは何？');
    assert.ok(runner.calls[0].cwd.includes(path.join('share', 'scratch')), 'workspace 無しは scratch で起こす');
    assert.ok(events.some((e) => e.channel === 'turn:done' && e.payload.id === sess.id));
    assert.ok(events.some((e) => e.channel === 'turn:progress' && /引受 b/.test(e.payload.item.text)));
    assert.equal(a.share.requester.get(request.id).state, 'done');
    assert.equal(b.share.ledger.today().count, 1);
    assert.equal(b.share.ledger.today().byRequester.a, 1);
    assert.equal(a.share.requester.served(), 1);
  });
});

test('先着 1 人だけ: 同じ依頼への 2 つ目の claim は 409。自分の依頼は自分で拾わない', async (t) => {
  await withNodes(t, async (open) => {
    const a = await open('a', { participate: true, clis: ['fake'] });
    const key = keyOf(PASS);
    const sess = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    const r = a.share.post({ sessionId: sess.id, goal: 'q', requires: { agent_cli: ['fake'] } });
    const me = { address: '127.0.0.1', port: a.share.port };
    const first = await call(me, 'POST', `/requests/${r.id}/claim`, { key, body: { who: 'x', port: 1, cli: 'fake' } });
    assert.equal(first.status, 200);
    assert.equal(first.body.goal, 'q');
    const second = await call(me, 'POST', `/requests/${r.id}/claim`, { key, body: { who: 'y', port: 1, cli: 'fake' } });
    assert.equal(second.status, 409);
    assert.equal(second.body.executor, 'x');
    const wrongKey = await call(me, 'GET', '/requests', { key: keyOf('other') });
    assert.equal(wrongKey.status, 401);
    const listed = await call(me, 'GET', '/requests', { key });
    assert.equal(listed.body[0].executor, 'x');
    assert.equal('goal' in listed.body[0], false, '一覧に本文は載せない');
    // 自分の依頼は自分の participant が拾わない
    await new Promise((res) => setTimeout(res, 300));
    assert.equal(a.share.participant.inflight.size, 0);
  });
});

test('心拍が途絶えたら列へ戻し、取り下げは執行者の CLI を止める', async (t) => {
  await withNodes(t, async (open) => {
    const a = await open('a');
    const key = keyOf(PASS);
    const sess = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    const r = a.share.post({ sessionId: sess.id, goal: 'q' });
    const me = { address: '127.0.0.1', port: a.share.port };
    assert.equal((await call(me, 'POST', `/requests/${r.id}/claim`, { key, body: { who: 'ghost', port: 1 } })).status, 200);
    await waitFor(() => a.share.requester.get(r.id).state === 'open', { timeoutMs: 3000 });
    assert.equal(a.share.requester.get(r.id).executor, null);

    const runner = fakeRunner([{ hang: true }]);
    const b = await open('b', { participate: true, clis: ['fake'], runPrompt: runner, seeds: [`127.0.0.1:${a.share.port}`] });
    await waitFor(() => b.share.participant.inflight.has(r.id));
    assert.equal(a.share.requester.get(r.id).state, 'working');
    assert.equal(await a.share.cancelSession(sess.id), true);
    await waitFor(() => !b.share.participant.inflight.has(r.id));
    const saved = store.readSession(a.userData, sess.id);
    assert.equal(saved.messages.at(-1).text, '（取り下げた）');
    assert.equal(saved.messages.at(-1).stopped, true);
    assert.equal(a.share.requester.get(r.id).state, 'cancelled');
    assert.equal(b.share.ledger.today().count, 0, '取り下げは件数に数えない');
  });
});

test('参加者の枠切れは 1 回だけ黙って別の参加者へ再投函し、切れた CLI はその日は受けない', async (t) => {
  await withNodes(t, async (open) => {
    const a = await open('a');
    const bRunner = fakeRunner([{ errorClass: 'quota', quotaKind: 'exhausted', error: '利用枠が枯渇' }]);
    const b = await open('b', { participate: true, clis: ['fake'], runPrompt: bRunner, seeds: [`127.0.0.1:${a.share.port}`] });
    await waitFor(() => a.share.peers.peers().some((p) => p.node === 'b'));
    const sess = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    const first = a.share.post({ sessionId: sess.id, goal: 'q' });
    await waitFor(() => a.share.requester.get(first.id).state === 'failed');
    const retried = await waitFor(() => [...a.share.requester.requests.values()].find((x) => x.retry_of === first.id));
    assert.equal(retried.attempts, 2);
    assert.equal(store.readSession(a.userData, sess.id).share.id, retried.id, '会話の印は新しい依頼へ');
    assert.equal(b.share.ledger.cliOk('fake'), false);
    assert.equal(b.share.participant.nodeInfo().clis.fake.can_accept, false);
    const cRunner = fakeRunner([{ text: 'ANSWER from c' }]);
    const c = await open('c', { participate: true, clis: ['fake'], runPrompt: cRunner, seeds: [`127.0.0.1:${b.share.port}`] });
    // c は b しか知らないが、ゴシップで a を知る
    await waitFor(() => c.share.peers.peers().some((p) => p.node === 'a'));
    const saved = await waitFor(() => { const s = store.readSession(a.userData, sess.id); return s.messages.some((m) => m.role === 'assistant') ? s : null; });
    assert.equal(saved.messages.at(-1).text, 'ANSWER from c');
    assert.equal(saved.messages.at(-1).share.node, 'c');
    assert.equal(bRunner.calls.length, 1, 'b は 2 回目を拾わない');
  });
});

test('依頼者 1 人あたりの上限と、添付の受け渡し', async (t) => {
  await withNodes(t, async (open) => {
    const a = await open('a');
    const runner = fakeRunner([{ text: 'ok' }]);
    const b = await open('b', { participate: true, clis: ['fake'], runPrompt: runner, seeds: [`127.0.0.1:${a.share.port}`], perRequesterDailyCap: 1 });
    await waitFor(() => b.share.peers.peers().some((p) => p.node === 'a'));
    const file = path.join(a.userData, 'spec.md');
    fs.writeFileSync(file, '# spec', 'utf8');
    const s1 = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    a.share.post({ sessionId: s1.id, goal: 'first', attachments: [{ name: 'spec.md', path: file }] });
    await waitFor(() => store.readSession(a.userData, s1.id).messages.some((m) => m.role === 'assistant'));
    assert.ok(/添付ファイル/.test(runner.calls[0].prompt));
    assert.equal(runner.calls[0].files.length, 1);
    const s2 = store.createSession(a.userData, { repo: '/repo', cli: 'fake' });
    const second = a.share.post({ sessionId: s2.id, goal: 'second' });
    await new Promise((res) => setTimeout(res, 400));
    assert.equal(a.share.requester.get(second.id).state, 'open', 'b は同じ依頼者の 2 件目を受けない');
    assert.equal(runner.calls.length, 1);
    const status = b.share.status();
    assert.equal(status.node, 'b');
    assert.equal(status.others.length, 1);
    assert.equal(status.others[0].host, 'a');
    assert.equal(status.today.byRequester.a, 1);
  });
});

test('再起動: 列は requests.json に残り、会話に印だけ残った依頼は失敗として閉じる', async (t) => {
  const userData = tmp('restart');
  const sess = store.createSession(userData, { repo: '/repo', cli: 'fake' });
  const config = settings.normalize({ share: { enabled: true, node: 'a', passphrase: PASS } });
  const mk = () => new Share({ userData, config, runPrompt: fakeRunner([{ text: 'x' }]), options: { udp: false, port: 0, host: '127.0.0.1', peers: { helloMs: 1000 } } });
  const first = mk();
  await first.start();
  const kept = first.post({ sessionId: sess.id, goal: 'keep' });
  store.updateSession(userData, sess.id, { share: { id: kept.id } });
  const orphan = store.createSession(userData, { repo: '/repo', cli: 'fake' });
  store.updateSession(userData, orphan.id, { share: { id: 'dg-lost' } });
  await first.stop();
  const second = mk();
  await second.start();
  t.after(() => second.stop());
  assert.equal(second.requester.get(kept.id).state, 'open');
  assert.equal(store.readSession(userData, sess.id).share.id, kept.id);
  const closed = store.readSession(userData, orphan.id);
  assert.equal(closed.share, null);
  assert.match(closed.messages.at(-1).text, /再起動で失われた/);
});

test('設定: share の正規化と、起動方針「共有」の解決', () => {
  const normalized = settings.normalize({ share: { enabled: true, node: ' Nitto ', port: 99999, peers: ['pc-b', 'pc-b', ' '], clis: ['Claude'], maxConcurrent: 9, dailyCap: -1, perRequesterDailyCap: 'x' } });
  assert.deepEqual(normalized.share, { enabled: true, node: 'Nitto', passphrase: '', port: 65535, udp: true, peers: ['pc-b'], participate: false, clis: ['claude'], acceptWrite: false, maxConcurrent: 4, dailyCap: 0, perRequesterDailyCap: 5 });
  assert.deepEqual(settings.resolve(normalized, { policy: 'shared', cli: '*', model: 'm' }), { policy: 'shared', tier: '', cli: '', model: 'm', source: 'shared' });
  assert.equal(settings.resolve(normalized, { policy: 'shared', cli: 'Claude' }).cli, 'claude');
  assert.throws(() => settings.resolve(settings.normalize({}), { policy: 'shared' }), /共有が設定されていません/);
  assert.equal(settings.effectivePolicy('shared', { optimized: false }), 'shared');
  const r = new Requester({ userData: tmp('req'), node: 'a', now: () => 0 });
  assert.equal(r.served(), 0);
});
