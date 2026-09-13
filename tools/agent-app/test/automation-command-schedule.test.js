'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/renderer/automation/renderer.js'), 'utf8');

function fixture() {
  const calls = [];
  const state = { root: '/project', config: { agent: 'codex', model: 'some-model' }, execution: {} };
  const ctx = vm.createContext({
    state, render() {}, esc: (s) => String(s),
    taskIdentity: (task) => task.id, taskSchedules: (task) => task.schedules || [],
    selectedExecutionMachine: () => state.execution.newCommand || state.task,
    executionMachines: () => state.task ? [state.task] : [],
    guard: async (_label, fn) => fn(),
    automationHost: { saveRunSchedule: async (root, payload) => { calls.push({ root, payload }); return { saved: true }; } },
    loadExecutionSnapshot: async () => {}, notifyHost() {}, toast() {},
    selectedAgent: () => { throw new Error('command must not use AI settings'); },
  });
  vm.runInContext(source.slice(source.indexOf('function commandText('), source.indexOf('async function toggleDaemon(')), ctx);
  return { ctx, state, calls };
}

test('new command schedule saves without AI settings and keeps typed values across frequency changes', async () => {
  const { ctx, state, calls } = fixture();
  ctx.newCommandSchedule();
  const draft = ctx.ensureScheduleDraft(state.execution.newCommand);
  const fields = {};
  for (const id of ['schedule-command', 'schedule-timeout', 'schedule-kind']) {
    fields[id] = { addEventListener: (_event, fn) => { fields[id].change = fn; } };
  }
  ctx.bindScheduleEditor({ querySelector: (id) => fields[id.slice(1)] || null, querySelectorAll: () => [] });
  fields['schedule-command'].value = 'python3 "scripts/two words.py"';
  fields['schedule-command'].change();
  fields['schedule-timeout'].value = '600';
  fields['schedule-timeout'].change();
  fields['schedule-kind'].value = 'interval';
  fields['schedule-kind'].change();
  draft.minutes = 15;
  assert.match(ctx.scheduleEditorHtml(state.execution.newCommand), /scripts\/two words.py/);
  await ctx.saveSchedule();
  const payload = JSON.parse(JSON.stringify(calls[0].payload));
  assert.deepEqual(payload.command, { argv: 'python3 "scripts/two words.py"', timeout_sec: 600 });
  assert.deepEqual(payload.schedule, { kind: 'interval', minutes: 15 });
  assert.equal(payload.operation, 'create');
  assert.ok(!('agentCli' in payload));
  assert.ok(!('model' in payload));
  assert.equal(state.execution.newCommand, null);
});

test('editing a command keeps environment and concurrency identity in the save request', async () => {
  const { ctx, state, calls } = fixture();
  state.task = { id: 'entry:old', kind: 'command', name: 'maintenance',
    entry: { command: { argv: ['python3', "it's here.py"], timeout_sec: 900, env: { SCOPE: 'home' } } },
    schedules: [{ entryRef: 'entry:old', fingerprint: 'fingerprint', kind: 'daily', time: '09:00', source: { scope: 'global' } }] };
  const draft = ctx.ensureScheduleDraft(state.task);
  draft.command = 'python3 changed.py';
  await ctx.saveSchedule();
  const payload = JSON.parse(JSON.stringify(calls[0].payload));
  assert.deepEqual(payload.command, { argv: 'python3 changed.py', timeout_sec: 900, env: { SCOPE: 'home' } });
  assert.equal(payload.entryRef, 'entry:old');
  assert.equal(payload.fingerprint, 'fingerprint');
  assert.equal(payload.destination, 'global');
  assert.equal(payload.operation, 'save');
});
