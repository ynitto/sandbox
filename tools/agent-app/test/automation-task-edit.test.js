"use strict";
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/renderer/automation/renderer.js'), 'utf8');
const navigation = require('../src/renderer/navigation');
const agentLoop = require('../src/main/automation/agent-loop');

function fixture() {
  const fields = {};
  for (const id of ['task-prompt-body', 'task-prompt-save', 'task-prompt-error']) {
    fields[id] = { value: '', addEventListener: (_event, fn) => { fields[id].click = fn; } };
  }
  const calls = [];
  const state = { root: '/project', run: {}, execution: {} };
  const ctx = vm.createContext({ state, Object,
    esc: (value) => String(value).replaceAll('<', '&lt;'),
    taskIdentity: (task) => task.id, taskSchedules: (task) => task.schedules || [],
    confirm: (message) => { calls.push(['confirm', message]); return true; },
    dialog: (_id, _title, _icon, html) => {
      calls.push(['dialog', html]);
      return { querySelector: (id) => fields[id.slice(1)], close: () => calls.push(['close']) };
    },
    automationHost: { mutateTask: async (root, request) => {
      calls.push(['mutate', root, request]); return { saved: true, deleted: true, entryRef: 'entry:new' };
    } },
    guard: async (_title, fn) => fn(),
    rememberRunParameters: async (...args) => calls.push(['forget', ...args]),
    loadMachines: async () => {}, loadExecutionSnapshot: async () => calls.push(['refresh']),
    notifyHost: (...args) => calls.push(['notify', ...args]), render() {}, toast() {},
  });
  vm.runInContext(source.slice(source.indexOf('function taskMutation('), source.indexOf('function embeddedTaskEditorHtml(')), ctx);
  return { ctx, state, calls, fields };
}

const promptTask = { id: 'entry:old', entryRef: 'entry:old', fingerprint: 'f', kind: 'prompt', name: 'digest', entry: { prompt: '<original>' } };

test('prompt editor saves entered text and keeps the newly identified task selected', async () => {
  const { ctx, calls, fields, state } = fixture();
  ctx.openTaskPrompt(promptTask);
  assert.match(calls[0][1], /&lt;original>/);
  fields['task-prompt-body'].value = 'new\nbody';
  await fields['task-prompt-save'].click();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.find(c => c[0] === 'mutate')[2])), {
    taskId: 'entry:old', action: 'update-prompt', entries: { 'entry:old': 'f' }, prompt: 'new\nbody',
  });
  assert.equal(state.execution.selected, 'entry:new');
  assert.ok(calls.some(c => c[0] === 'refresh'));
});

test('failed prompt save retains the text and displays the error', async () => {
  const { ctx, calls, fields } = fixture();
  ctx.automationHost.mutateTask = async () => { throw new Error('再読み込みしてください'); };
  ctx.openTaskPrompt(promptTask);
  fields['task-prompt-body'].value = 'keep draft';
  await fields['task-prompt-save'].click();
  assert.equal(fields['task-prompt-body'].value, 'keep draft');
  assert.equal(fields['task-prompt-error'].textContent, '再読み込みしてください');
  assert.equal(fields['task-prompt-save'].disabled, false);
  assert.ok(!calls.some(c => c[0] === 'close'));
});

test('every task kind deletes registrations through the task API and clears remembered inputs', async () => {
  for (const kind of ['prompt', 'command', 'hook', 'broken', 'statemachine']) {
    const { ctx, calls, state } = fixture();
    const task = kind === 'statemachine'
      ? { id: 'machine:digest', machine: 'digest', name: 'digest', kind, schedules: [{ entryRef: 's1', fingerprint: 'f1' }, { entryRef: 's2', fingerprint: 'f2' }] }
      : { ...promptTask, kind };
    await ctx.deleteTask(task);
    const mutation = calls.find(c => c[0] === 'mutate')[2];
    assert.equal(mutation.action, 'delete');
    assert.equal(mutation.taskId, task.id);
    assert.ok(calls.some(c => c[0] === 'forget'));
    assert.ok(calls.some(c => c[0] === 'refresh'));
    assert.equal(state.execution.scheduleDraft, null);
    if (kind === 'statemachine') assert.match(calls[0][1], /ステートマシン本体は残ります/);
  }
});

test('deleted machines cannot return from definition or teaching fallbacks', () => {
  const definition = { machine: 'digest', name: 'digest' };
  assert.deepEqual(navigation.taskItems({ tasks: [], deletedMachines: ['digest'] }, [definition], [definition]), []);
  const ctx = vm.createContext({ state: { execution: { snapshot: { tasks: [], machines: [] } }, machines: [definition] } });
  vm.runInContext(source.slice(source.indexOf('function savedExecutionMachines('), source.indexOf('function taskIdentity(')), ctx);
  assert.equal(ctx.savedExecutionMachines().length, 0);
});

test('task mutation checks runtime capability and sends a JSON request', async () => {
  const calls = [];
  const payload = { action: 'delete', taskId: 'entry:old', entries: {} };
  const capture = async (command, args, opts) => {
    calls.push({ command, args, opts });
    return { ok: true, stdout: JSON.stringify(args[0] === 'inspect' ? { capabilities: { taskMutation: true } } : { deleted: true }) };
  };
  assert.equal((await agentLoop.mutateTask({ root: '/project', payload, capture })).deleted, true);
  assert.deepEqual(calls[1].args, ['task', '--json', '--dir', '/project']);
  assert.deepEqual(JSON.parse(calls[1].opts.input), payload);
  await assert.rejects(agentLoop.mutateTask({ root: '/project', payload,
    capture: async () => ({ ok: true, stdout: '{}' }) }), /更新が必要/);
});
