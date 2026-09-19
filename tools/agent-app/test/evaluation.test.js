'use strict';

// 応答と実行の評価（src/main/evaluation.js）と、その申告（audit.feedEvaluation / used）。
// 設計: docs/plans/2026-09-19-agent-app-opik-equivalent-observability-design.md。
// 判定 AI（agent-herd judge）は偽の capture で差し替える。数字はここで作らず、行を書くまでを見る。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const evaluation = require('../src/main/evaluation');
const audit = require('../src/main/audit');
const settings = require('../src/main/settings');
const runHistory = require('../src/main/automation/run-history');

function tmp() { return fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-eval-')); }
function feedRows(userData) {
  const dir = audit.feedDir(userData);
  try {
    return fs.readdirSync(dir).flatMap((name) => fs.readFileSync(path.join(dir, name), 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)));
  } catch { return []; }
}
const judgeOut = ({ score = 2.6, issue = 'skill-gap', confidence = 0.8, abstained = [] } = {}) => JSON.stringify({
  answers: {
    quality: { type: 'score', score, bucket: String(Math.round(score)), confidence, coverage: 0.9, method: 'logprobs' },
    issue: { type: 'choice', choice: issue, confidence, coverage: 0.9, method: 'logprobs' },
  },
  abstained,
});
function fakeCapture({ herd = true, out = judgeOut(), status = 0, calls = [] } = {}) {
  return async (name, args, opts) => {
    calls.push({ name, args, input: opts && opts.input });
    if (name === 'agent-herd' && args[0] === 'config') return { ok: herd, status: herd ? 0 : 127, stdout: '{}', stderr: '' };
    if (name === 'agent-herd' && args[0] === 'judge') return { ok: status === 0, status, stdout: out, stderr: '@agent-usage tokens_in=1 tokens_out=1' };
    if (name === 'agent-audit' && args[0] === 'scrub') return { ok: true, status: 0, stdout: String(opts.input).replace(/token=\S+/g, 'token=[REDACTED]'), stderr: '' };
    return { ok: false, status: 127, stdout: '', stderr: 'unknown' };
  };
}
function makeEvaluator(over = {}) {
  const userData = tmp();
  const calls = [];
  const capture = over.capture || fakeCapture({ calls, ...(over.fake || {}) });
  const ev = new evaluation.Evaluator({
    userData, loadConfig: () => ({ evaluation: { mode: over.mode || 'all' } }), capture,
    runPrompt: over.runPrompt || null, readRecord: over.readRecord || null,
    busy: over.busy || (() => false), post: () => {}, now: () => Date.parse('2026-09-19T00:00:00Z'),
    setTimer: (fn) => { fn(); return null; }, clearTimer: () => {},
  });
  return { ev, userData, calls };
}

test('設定: evaluation.mode は sample / all / off。知らない値は sample', () => {
  assert.equal(settings.normalize({}).evaluation.mode, 'sample');
  assert.equal(settings.normalize({ evaluation: { mode: 'off' } }).evaluation.mode, 'off');
  assert.equal(settings.normalize({ evaluation: { mode: 'weird' } }).evaluation.mode, 'sample');
});

test('標本: 失敗は必ず、成功は sample なら 5 件に 1 件、all は全部、off は無し', () => {
  assert.equal(evaluation.shouldEvaluate('off', { failed: true }), false);
  assert.equal(evaluation.shouldEvaluate('sample', { failed: true, counter: 3 }), true);
  assert.deepEqual([0, 1, 4, 5, 9, 10].map((c) => evaluation.shouldEvaluate('sample', { counter: c })), [true, false, false, true, false, true]);
  assert.equal(evaluation.shouldEvaluate('all', { counter: 3 }), true);
});

test('状態の本文: 依頼の先頭と回答の末尾を残し、コマンドと使ったものを添える', () => {
  const text = evaluation.stateText({
    prompt: 'p'.repeat(10000), answer: 'a'.repeat(10000) + 'END',
    information: [{ type: 'command', title: 'npm test', status: 'error' }, { type: 'file', title: 'x' }],
    used: { skills: ['statemachine-use'], tools: ['npm'] }, status: '失敗', error: 'boom',
  });
  assert.ok(text.length < evaluation.STATE_CHARS + 400);
  assert.match(text, /## 依頼\np+…/);
  assert.match(text, /aaaEND\n/);
  assert.match(text, /- npm test（失敗）/);
  assert.match(text, /スキル: statemachine-use/);
  assert.match(text, /## 結果: 失敗 — boom/);
});

test('judge の出力: 2 つの問いを 1 つの評価にする。棄権していれば null', () => {
  const ok = evaluation.parseJudge(judgeOut({ score: 2.6, issue: 'tool-failure', confidence: 0.7 }), { model: 'gemma4:e4b' });
  assert.deepEqual(ok, { quality: 3, issue: 'tool-failure', confidence: 0.7, method: 'logprobs', judge_model: 'gemma4:e4b' });
  assert.equal(evaluation.parseJudge(judgeOut({ abstained: ['issue'] })), null);
  assert.equal(evaluation.parseJudge('garbage'), null);
  // 知らない issue は none に倒す（agent-audit の語彙の外を書かない）
  assert.equal(evaluation.parseJudge(judgeOut({ issue: 'weird' })).issue, 'none');
});

test('ヘッドレスの出力: JSON だけを拾い、quality が 1〜3 でなければ null', () => {
  assert.deepEqual(evaluation.parseHeadless('前置き\n{"quality": 2, "issue": "prompt-issue", "note": "曖昧"}\n後書き', { model: 'claude' }),
    { quality: 2, issue: 'prompt-issue', confidence: 1, method: 'text', judge_model: 'claude', note: '曖昧' });
  assert.equal(evaluation.parseHeadless('{"quality": 7, "issue": "none"}'), null);
  assert.match(evaluation.headlessPrompt('STATE'), /JSON だけ[\s\S]*STATE$/);
});

test('会話へ渡す依頼文: 対象・課題・根拠を載せ、改善案は載せない', () => {
  const prompt = evaluation.handoffPrompt({
    id: 'ins-1', target: { kind: 'skill', name: 'statemachine-use' }, statement: '見本の記録を 2 回以上やり直している',
    occurrences: 6, confidence: 'medium', kind: 'skill-improvement', evidence: ['obs-1', 'obs-2'],
  });
  assert.match(prompt, /まだ直さなくてよい/);
  assert.match(prompt, /## 対象\nスキル「statemachine-use」/);
  assert.match(prompt, /## 課題\n見本の記録を 2 回以上やり直している/);
  assert.match(prompt, /観測 6 件 · 確度 medium/);
  assert.match(prompt, /obs-1, obs-2/);
  assert.ok(!/suggested_action|rules\.md/.test(prompt));
  assert.match(evaluation.handoffPrompt({ statement: 'x' }), /## 対象\n全体/);
});

test('申告: 会話のターンに used（スキル・コマンド・ツール）が載る', () => {
  const message = {
    role: 'assistant', cli: 'claude', text: 'ok', elapsedMs: 1200,
    skillSelection: [{ name: 'statemachine-use' }],
    parts: { information: [{ type: 'command', title: 'npm test' }, { type: 'command', title: '/usr/bin/git diff' }, { type: 'file', title: 'a' }] },
  };
  assert.deepEqual(audit.usedOf(message), { skills: ['statemachine-use'], commands: ['npm test', '/usr/bin/git diff'], tools: ['npm', 'git'] });
  assert.equal(audit.usedOf({ role: 'assistant', text: 'x' }), null);
  const userData = tmp();
  const rec = audit.feedTurn(userData, { session: { id: 's1', cli: 'claude' }, message });
  assert.deepEqual(rec.used, { skills: ['statemachine-use'], commands: ['npm test', '/usr/bin/git diff'], tools: ['npm', 'git'] });
});

test('申告: 評価は workload: evaluation の別の 1 行で、評価された側の行は書き換えない', () => {
  const userData = tmp();
  audit.feedTurn(userData, { session: { id: 's1', cli: 'claude' }, message: { role: 'assistant', text: 'x', cli: 'claude' } });
  const rec = audit.feedEvaluation(userData, {
    evaluated: { workload: 'chat', ref: 's1', agent_cli: 'claude', model: 'sonnet', used: { skills: ['s'] } },
    evaluation: { quality: 1, confidence: 0.8, issue: 'skill-gap', method: 'logprobs', judge_model: 'gemma4:e4b' },
  });
  assert.equal(rec.workload, 'evaluation');
  assert.equal(rec.purpose, 'chat');
  assert.equal(rec.status, 'done');
  assert.deepEqual(rec.evaluation, { quality: 1, confidence: 0.8, issue: 'skill-gap', method: 'logprobs', judge_model: 'gemma4:e4b', note: '' });
  const rows = feedRows(userData);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].workload, 'chat');
  assert.equal(rows[0].evaluation, undefined);
});

test('自動評価: 応答が終わると judge に訊き、問題ありなら評価の行が残る。失敗は標本に関係なく評価する', async () => {
  const { ev, userData, calls } = makeEvaluator({ mode: 'sample' });
  const message = { role: 'assistant', cli: 'claude', text: '回答', error: 'boom', code: 1, parts: { information: [{ type: 'command', title: 'npm test', status: 'error' }] } };
  for (let i = 0; i < 3; i += 1) assert.equal(ev.noteTurn({ session: { id: 's1', messages: [{ role: 'user', text: '依頼' }] }, message }), true);
  await ev.drain();
  const judged = calls.filter((c) => c.name === 'agent-herd' && c.args[0] === 'judge');
  assert.equal(judged.length, 3);
  assert.match(judged[0].input, /## 依頼\n依頼\n\n## 回答\n回答/);
  assert.match(judged[0].args[2], /"quality"[\s\S]*"issue"/);
  const rows = feedRows(userData);
  assert.equal(rows.length, 3);
  assert.equal(rows[0].workload, 'evaluation');
  assert.equal(rows[0].ref, 's1');
  assert.equal(rows[0].evaluation.issue, 'skill-gap');
  assert.deepEqual(rows[0].used, { commands: ['npm test'], tools: ['npm'] });
  assert.equal(ev.status().evaluated, 3);
  assert.equal(ev.status().issues, 3);
});

test('自動評価: sample は成功した応答を 5 件に 1 件だけ、off は何もしない', async () => {
  const { ev, userData } = makeEvaluator({ mode: 'sample' });
  const ok = { role: 'assistant', cli: 'claude', text: 'x' };
  const taken = [];
  for (let i = 0; i < 10; i += 1) taken.push(ev.noteTurn({ session: { id: 's' }, message: ok }));
  assert.deepEqual(taken, [true, false, false, false, false, true, false, false, false, false]);
  await ev.drain();
  assert.equal(feedRows(userData).length, 2);
  const off = makeEvaluator({ mode: 'off' });
  assert.equal(off.ev.noteTurn({ session: { id: 's' }, message: { ...ok, error: 'x' } }), false);
});

test('自動評価: judge が棄権したら行を書かない。agent-herd が無ければ何もしない', async () => {
  const held = makeEvaluator({ fake: { out: judgeOut({ abstained: ['quality'] }), status: 1 } });
  held.ev.noteTurn({ session: { id: 's' }, message: { role: 'assistant', text: 'x', error: 'e' } });
  await held.ev.drain();
  assert.equal(feedRows(held.userData).length, 0);
  assert.equal(held.ev.status().lastError, '');
  const none = makeEvaluator({ fake: { herd: false } });
  none.ev.noteTurn({ session: { id: 's' }, message: { role: 'assistant', text: 'x', error: 'e' } });
  await none.ev.drain();
  assert.equal(feedRows(none.userData).length, 0);
  assert.match(none.ev.status().lastError, /agent-herd/);
});

test('自動評価: ターンが動いている間は回さず、後でやり直す', async () => {
  let busy = true;
  const timers = [];
  const userData = tmp();
  const ev = new evaluation.Evaluator({
    userData, loadConfig: () => ({ evaluation: { mode: 'all' } }), capture: fakeCapture(),
    busy: () => busy, post: () => {}, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
  });
  ev.noteTurn({ session: { id: 's' }, message: { role: 'assistant', text: 'x' } });
  timers.shift().fn();               // 最初の tick: busy なので延期
  await Promise.resolve();
  assert.equal(feedRows(userData).length, 0);
  assert.equal(timers.at(-1).ms, 30000);
  busy = false;
  await ev.drain();
  assert.equal(feedRows(userData).length, 1);
});

test('実行の評価: run-history に記録すると申告の聞き手として評価に回り、成果物が対象になる', async () => {
  const { ev, userData } = makeEvaluator({ mode: 'all' });
  const off = audit.onFeed((rec, raw, extra) => { if (rec.workload === 'task' && extra && extra.record) ev.noteRun(extra); });
  try {
    runHistory.append(userData, '/repo/demo', { taskId: 'machine:monthly', machine: 'monthly', ok: false, errorClass: 'verify', startedAt: '2026-09-19T00:00:00Z', finishedAt: '2026-09-19T00:01:00Z', agentCli: 'codex' });
  } finally { off(); }
  await ev.drain();
  const rows = feedRows(userData);
  assert.deepEqual(rows.map((r) => r.workload), ['task', 'evaluation']);
  assert.deepEqual(rows[1].artifact, { kind: 'task', name: 'monthly', origin: 'repo:demo' });
  assert.equal(rows[1].purpose, 'task');
});

test('まとめて評価: 検索の record を読み、最後の往復を評価する。上限と進み具合', async () => {
  const records = {
    'app:1': { appId: '1', agent: 'claude', model: 'sonnet', repo: '/repo', messages: [{ role: 'user', text: 'q1' }, { role: 'assistant', text: 'a1', parts: { information: [{ type: 'command', title: 'npm test' }] } }] },
    'cli:2': { agent: 'codex', nativeId: 'n2', messages: [{ role: 'user', text: 'q2' }, { role: 'assistant', text: 'a2' }] },
    'cli:3': { agent: 'codex', messages: [{ role: 'user', text: 'no answer' }] },
  };
  const { ev, userData, calls } = makeEvaluator({ readRecord: async (key) => { if (!records[key]) throw new Error('nf'); return records[key]; } });
  await assert.rejects(ev.startBatch({ keys: [] }), /選んで/);
  await assert.rejects(ev.startBatch({ keys: Array.from({ length: 201 }, (_, i) => `k${i}`) }), /200 件/);
  const status = await ev.startBatch({ keys: ['app:1', 'cli:2', 'cli:3', 'missing'], cli: 'herd' });
  assert.equal(status.batch.total, 4);
  await ev.drain();
  const done = ev.status().batch;
  assert.equal(done.running, false);
  assert.equal(done.done, 2);
  assert.equal(done.skipped, 2);
  assert.equal(done.issues, 2);
  const rows = feedRows(userData);
  assert.deepEqual(rows.map((r) => [r.ref, r.agent_cli, r.session_id || '']), [['1', 'claude', ''], ['cli:2', 'codex', 'n2']]);
  assert.equal(calls.filter((c) => c.args[0] === 'judge').length, 2);
});

test('まとめて評価: クラウドの AI を選ぶと伏せ字化してからヘッドレス 1 回（読み取り専用）', async () => {
  const prompts = [];
  const runPrompt = ({ cli, prompt, readonly }) => { prompts.push({ cli, prompt, readonly }); return { done: Promise.resolve({ text: '{"quality": 2, "issue": "prompt-issue"}', code: 0, error: '' }), stop() {} }; };
  const record = { appId: '1', agent: 'claude', messages: [{ role: 'user', text: 'token=SECRET123' }, { role: 'assistant', text: 'a' }] };
  const { ev, userData, calls } = makeEvaluator({ runPrompt, readRecord: async () => record });
  await ev.startBatch({ keys: ['app:1'], cli: 'claude', model: 'sonnet' });
  await ev.drain();
  assert.equal(prompts.length, 1);
  assert.equal(prompts[0].readonly, true);
  assert.equal(prompts[0].cli, 'claude');
  assert.ok(!prompts[0].prompt.includes('SECRET123'), '伏せ字化してから渡す');
  assert.ok(calls.some((c) => c.name === 'agent-audit' && c.args[0] === 'scrub'));
  const rows = feedRows(userData);
  assert.equal(rows[0].evaluation.issue, 'prompt-issue');
  assert.equal(rows[0].evaluation.judge_model, 'claude:sonnet');
  assert.equal(rows[0].evaluation.method, 'text');
});
