'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/flowTeaching.js'), 'utf8');

function setup(sessionPromise) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, disabled: false, value: id === 'flow-teach-agent' ? 'codex' : '',
      dataset: {}, options: [{ value: 'codex' }], classList: { toggle() {} },
      setAttribute() {}, replaceChildren() {}, addEventListener() {}, focus() {},
    });
    return nodes.get(id);
  };
  let reads = 0;
  let detaches = 0;
  let attached = '';
  const term = {
    detach() { detaches += 1; attached = ''; }, current() { return attached; },
    async attach(id) { attached = id; }, size: () => ({ cols: 80, rows: 24 }),
    setInputEnabled() {}, refit() {}, configure() {},
  };
  const context = {
    window: { FlowTerm: term }, document: { getElementById: node, querySelectorAll: () => [] },
    api: { automation: { flowTeachSession() { reads += 1; return sessionPromise; } } },
    ExecutionChoice: { sync() {} },
    InputMode: { create: () => ({ mode: 'message' }), reduce: (_, event) => ({ mode: event.type.replace('-focus', '') }) },
    clearTimeout() {}, setTimeout() {}, requestAnimationFrame(callback) { callback(); },
  };
  vm.runInNewContext(source, context);
  context.window.FlowTeaching.state.deps = {
    notice() {}, agentNames: () => ['codex'], executionDefaults: () => ({ agent: 'codex' }),
    executionLabel: () => 'codex', modelNames: () => [], shareEnabled: () => false, isRunning: () => false,
  };
  return { teaching: context.window.FlowTeaching, api: context.api, node, term, reads: () => reads, detaches: () => detaches };
}

test('workflow redraw keeps an in-flight session load', async () => {
  let resolveSession;
  const pending = new Promise((resolve) => { resolveSession = resolve; });
  const { teaching, reads, detaches } = setup(pending);
  const detail = { root: '/repo', workflowId: 'example', existing: false };
  teaching.show(detail);
  const ready = teaching.state.ready;
  teaching.show({ ...detail, existing: true });
  assert.equal(reads(), 1);
  assert.equal(detaches(), 1);
  assert.equal(teaching.state.ready, ready);
  resolveSession({ session: { id: 'session-1', cli: 'codex', model: '', autoApprove: false } });
  await ready;
  assert.equal(teaching.state.availableSession.id, 'session-1');
});

test('workflow redraw keeps an attached terminal when draft status changes', async () => {
  const { teaching, reads, detaches } = setup(Promise.resolve({ session: null }));
  teaching.show({ root: '/repo', workflowId: 'example', existing: false });
  await teaching.state.ready;
  teaching.state.session = { id: 'session-1', cli: 'codex', model: '' };
  teaching.show({ root: '/repo', workflowId: 'example', existing: true });
  assert.equal(reads(), 1);
  assert.equal(detaches(), 1);
  assert.equal(teaching.state.session.id, 'session-1');
});

test('returning to a workflow reloads its session', async () => {
  const { teaching, reads } = setup(Promise.resolve({ session: null }));
  const detail = { root: '/repo', workflowId: 'example', existing: false };
  teaching.show(detail);
  await teaching.state.ready;
  teaching.show({ hidden: true });
  teaching.show(detail);
  await teaching.state.ready;
  assert.equal(reads(), 2);
});

test('workflow redraw does not cancel an in-flight start', async () => {
  let finishStart;
  const started = new Promise((resolve) => { finishStart = resolve; });
  const { teaching, api, node, reads } = setup(Promise.resolve({ session: { id: 'session-1', cli: 'codex' } }));
  teaching.state.deps.executionOptions = () => ({ allocation: 'auto' });
  teaching.state.deps.reloadWorkflows = async () => {};
  api.automation.flowTeachStart = () => started;
  teaching.show({ root: '/repo', workflowId: 'example', existing: false });
  await teaching.state.ready;
  teaching.init(teaching.state.deps);
  const opening = node('flow-teach-start').onclick();
  assert.equal(teaching.state.pending, true);
  teaching.show({ root: '/repo', workflowId: 'example', existing: false });
  assert.equal(teaching.state.pending, true);
  assert.equal(reads(), 1);
  finishStart({ session: null, started: true });
  await opening;
  assert.equal(teaching.state.pending, false);
});

test('first edit prepares and attaches the terminal before sending the turn', async () => {
  let releaseTurn;
  let turnStarted;
  const started = new Promise((resolve) => { turnStarted = resolve; });
  const { teaching, api, node, term } = setup(Promise.resolve({ session: null }));
  teaching.state.deps.executionOptions = () => ({ policy: 'direct', cli: 'codex', model: '' });
  teaching.state.deps.reloadWorkflows = async () => {};
  const calls = [];
  const session = { id: 'session-1', cli: 'codex', model: '', policy: 'direct' };
  api.automation.flowTeachPrepare = async () => { calls.push('prepare'); return { session }; };
  api.termOpen = async () => { calls.push('open'); return { phase: 'ready' }; };
  const originalAttach = term.attach;
  term.attach = async (id) => { calls.push('attach'); await originalAttach(id); };
  api.automation.flowTeachStart = () => {
    calls.push('start');
    turnStarted();
    return new Promise((resolve) => { releaseTurn = resolve; });
  };
  teaching.show({ root: '/repo', workflowId: 'existing', existing: true });
  await teaching.state.ready;
  teaching.init(teaching.state.deps);
  const opening = node('flow-teach-start').onclick();
  await started;
  assert.deepEqual(calls, ['prepare', 'open', 'attach', 'start']);
  assert.equal(term.current(), session.id);
  releaseTurn({ session, started: true });
  await opening;
});

test('failed terminal connection keeps a visible retry action and restart reconnects', async () => {
  const { teaching, api, node, term } = setup(Promise.resolve({ session: null }));
  teaching.state.deps.executionOptions = () => ({ policy: 'direct', cli: 'codex', model: '' });
  teaching.state.deps.reloadWorkflows = async () => {};
  const session = { id: 'session-1', cli: 'codex', model: '', policy: 'direct' };
  api.automation.flowTeachPrepare = async () => ({ session });
  api.automation.flowTeachStart = async () => ({ session, started: false });
  api.termOpen = async () => { throw new Error('tmux unavailable'); };
  teaching.show({ root: '/repo', workflowId: 'existing', existing: true });
  await teaching.state.ready;
  teaching.init(teaching.state.deps);
  await node('flow-teach-start').onclick();
  assert.equal(teaching.state.phase.phase, 'gone');
  assert.equal(node('flow-teach-restart').hidden, false);
  assert.equal(term.current(), '');

  api.termRestart = async () => ({ phase: 'ready', name: 'recovered' });
  await node('flow-teach-restart').onclick();
  assert.equal(teaching.state.phase.phase, 'ready');
  assert.equal(term.current(), session.id);
  assert.equal(node('flow-teach-restart').hidden, true);
});
