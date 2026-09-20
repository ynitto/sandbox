"use strict";
const { test } = require('node:test');
const assert = require('node:assert/strict');
const q = require('../src/main/quality-evaluation');
const { Evaluator } = require('../src/main/evaluation');
const audit = require('../src/main/audit');
const a = (choice, extra = {}) => ({ choice, method: 'logprobs', confidence: .9, coverage: .95, ...extra });
const input = { prompt: '- collect\n- render', criteria: ['collect', 'render'], answer: 'collector exists\nrenderer missing', inventoryComplete: true };

function response() { return { answers: { c1: a('met'), c1_evidence: a('e1'), c2: a('unmet'), c2_evidence: a('e2') } }; }

test('explicit list criteria only; prose is unknown without judge calls', async () => {
  assert.deepEqual(q.explicitCriteria('Intro\n- collect\n2. render'), ['collect', 'render']);
  let calls = 0;
  const p = await q.evaluate({ prompt: 'Do a good job', answer: 'done' }, async () => { calls++; });
  assert.equal(p.status, 'unknown'); assert.equal(p.reason, 'no_explicit_criteria'); assert.equal(calls, 0);
});

test('per-condition findings keep real evidence IDs, not one overall quality score', () => {
  const p = q.finalize(q.prepare(input), response());
  assert.equal(p.status, 'problem'); assert.equal(p.cause, 'unknown');
  assert.deepEqual(p.checks.map(c => c.status), ['met', 'unmet']);
  assert.equal(p.evidence.find(e => e.id === p.checks[1].evidence_id).text, 'renderer missing');
});

test('missing/invalid citation, low confidence, low coverage and text all become unknown', () => {
  for (const extra of [{ method: 'text' }, { method: 'vote' }, { confidence: .69 }, { coverage: .79 }, { confidence: NaN }]) {
    const r = response(); r.answers.c2 = a('unmet', extra);
    assert.equal(q.finalize(q.prepare(input), r).checks[1].status, 'unknown');
  }
  const r = response(); r.answers.c2_evidence = a('invented');
  assert.equal(q.finalize(q.prepare(input), r).checks[1].status, 'unknown');
});

test('inventory citation cannot establish success, or absence in an incomplete record', () => {
  const r = response(); r.answers.c1_evidence = a('inventory');
  assert.equal(q.finalize(q.prepare(input), r).checks[0].status, 'unknown');
  r.answers.c2_evidence = a('inventory');
  assert.equal(q.finalize(q.prepare({ ...input, inventoryComplete: false }), r).checks[1].status, 'unknown');
  assert.equal(q.finalize(q.prepare(input), r).checks[1].status, 'unmet');
});

test('mapped command exit status overrides judge; arbitrary pass text is not a receipt', () => {
  const plan = q.prepare({ ...input, verification: [
    { criterion: 'c1', command: 'test collector', exitCode: 1, completed: true },
    { criterion: 'c2', command: 'test renderer', result: 'pass' },
  ] });
  assert.equal(plan.questions.c1, undefined);
  const p = q.finalize(plan, response());
  assert.equal(p.checks[0].basis, 'verification'); assert.equal(p.checks[0].status, 'unmet');
  assert.equal(p.checks[1].basis, 'judge');
});

test('truncation cannot produce a supported decision', () => {
  const plan = q.prepare({ prompt: '', criteria: ['x'], answer: 'x'.repeat(901) });
  const p = q.finalize(plan, { answers: { c1: a('met'), c1_evidence: a('e1') } });
  assert.equal(p.status, 'unknown'); assert.equal(p.reason, 'truncated_input');
});

test('cause runs only after a problem and requires actual command-event evidence', async () => {
  const questions = [];
  const p = await q.evaluate({ ...input, information: [{ type: 'command', title: 'npm test', status: 'error' }] },
    async (_state, qs) => { questions.push(qs); return qs.cause ? { answers: { cause: a('tool-failure') } } : response(); });
  assert.equal(questions.length, 2); assert.equal(p.cause, 'tool-failure'); assert.equal(p.cause_evidence_id, 'cmd1');
  const noEvents = q.finalize(q.prepare(input), response());
  assert.equal(q.finalizeCause(noEvents, { answers: { cause: a('tool-failure') } }).cause, 'unknown');
  assert.equal(q.causeRequest({ status: 'unknown' }), null);
});

test('request failures remain unknown with error metadata', async () => {
  const p = await q.evaluate(input, async () => { throw new Error('connection refused'); });
  assert.equal(p.status, 'unknown'); assert.equal(p.error.kind, 'request_failure');
});

test('advisory automatic path records a proposal without a fabricated quality score', async () => {
  const records = [];
  const ev = new Evaluator({ userData: '/tmp/unused', loadConfig: () => ({ evaluation: { mode: 'all', strategy: 'evidence-advisory' } }),
    capture: async (_name, args) => ({ ok: true, stdout: JSON.stringify(JSON.parse(args[args.indexOf('--questions') + 1]).cause ? { answers: { cause: a('unknown') } } : response()) }),
    feed: (_dir, rec) => { records.push(rec); return rec; }, busy: () => false, post: () => {},
    setTimer: () => null, clearTimer: () => {} });
  await ev.evaluateOne({ kind: 'auto', evaluated: {}, qualityInput: input, state: '' });
  assert.equal(records[0].evaluation.proposal.status, 'problem'); assert.equal(records[0].evaluation.quality, null);
  assert.equal(records[0].evaluation.issue, 'none'); assert.equal(ev.issues, 1);
  const persisted = audit.row({ evaluation: records[0].evaluation });
  assert.equal(persisted.evaluation.quality, null); assert.equal(persisted.evaluation.proposal.status, 'problem');
});

test('switching off drains no pending automatic evaluation', async () => {
  let calls = 0;
  const ev = new Evaluator({ userData: '/tmp/unused', loadConfig: () => ({ evaluation: { mode: 'off' } }),
    capture: async () => { calls++; }, busy: () => false, post: () => {}, setTimer: () => null, clearTimer: () => {} });
  ev.queue.push({ kind: 'auto' }); await ev.drain(); assert.equal(calls, 0);
});


test('malformed criteria and clipped request cannot produce supported', () => {
  const q = require('../src/main/quality-evaluation');
  assert.equal(q.finalize(q.prepare({ criteria: [null, {}] })).status, 'unknown');
  assert.equal(q.prepare({ prompt: 'x'.repeat(3001), criteria: ['a'] }).truncated, true);
});
