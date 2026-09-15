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
  vm.runInContext(source.slice(source.indexOf('function manualTaskHtml('), source.indexOf('async function toggleDaemon(')), ctx);
  return { ctx, state, calls };
}

test('new command schedule saves without AI settings and keeps typed values across frequency changes', async () => {
  const { ctx, state, calls } = fixture();
  ctx.createManualTask();
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

test('保存後の親一覧は作成したコマンドを選択し、定義だけの一覧へ戻さない', async () => {
  const parent = fs.readFileSync(path.join(__dirname, '../src/renderer/renderer.js'), 'utf8');
  const state = { repo: '/project', area: 'tasks', config: { lastTask: { '/project': 'machine:old' } }, taskToken: 0 };
  const tasks = [{ id: 'machine:old' }, { id: 'entry:new' }];
  const ctx = vm.createContext({ state,
    taskId: (t) => t?.id || '', renderAreaContext() {},
    AgentNavigation: { taskItems: (snapshot) => snapshot.tasks },
    api: { saveConfig: async (patch) => ({ ...state.config, ...patch }), automation: {
      runSnapshot: async () => ({ tasks }), listMachines: async () => [tasks[0]], teachingList: async () => [],
    } },
    loadAreaItems: () => { throw new Error('定義だけで再選択してはいけない'); },
  });
  vm.runInContext(parent.slice(parent.indexOf('function pickSelectedTask('), parent.indexOf('// 実行状態（agent-loop')), ctx);
  vm.runInContext(parent.slice(parent.indexOf('async function handleAutomationEvent('), parent.indexOf('async function selectAreaItem(')), ctx);
  await ctx.handleAutomationEvent({ type: 'agent-app:changed', root: '/project', area: 'tasks', selected: 'entry:new' });
  assert.equal(state.selectedTask, 'entry:new');
  assert.equal(state.tasks.length, 2);
});

test('複雑なcronでも名前とコマンドを編集するフォームが開く', () => {
  const { ctx } = fixture();
  const task = { id: 'entry:cron', kind: 'command', name: 'hourly', entry: { command: ['echo', 'ok'] },
    schedules: [{ entryRef: 'entry:cron', fingerprint: 'f', advanced: true, kind: 'advanced' }] };
  const html = ctx.scheduleEditorHtml(task);
  assert.match(html, /id="schedule-command"/);
  assert.match(html, /id="schedule-name"/);
  assert.equal(ctx.ensureScheduleDraft(task).kind, 'preserve');
});


test('WSLの状態取得待ち・失敗でもコマンド作成フォームを開ける', () => {
  for (const snapshot of [null, { available: false }, { available: true }]) {
    const { ctx, state } = fixture();
    state.execution.snapshot = snapshot;
    ctx.createManualTask();
    assert.equal(state.homeTab, 'manual');
    assert.match(ctx.manualTaskHtml(), /id="schedule-command"/);
    assert.equal(state.execution.newCommand.kind, 'command');
    assert.equal(state.execution.scheduleOpen, true);
    assert.match(ctx.scheduleEditorHtml(state.execution.newCommand), /id="schedule-command"/);
  }
  const { ctx, state } = fixture();
  state.root = '';
  ctx.createManualTask();
  assert.equal(state.execution.newCommand, undefined);
});


test('advanced AI schedules edit agent/model independently from manual settings', async () => {
  const { ctx, state, calls } = fixture();
  state.agents = ['codex', 'claude'];
  state.task = { id: 'entry:ai', kind: 'prompt', name: 'review', entry: { prompt: 'review' },
    schedules: [{ entryRef: 'entry:ai', fingerprint: 'v1', advanced: true, agentCli: 'claude', model: 'saved-model' }] };
  const draft = ctx.ensureScheduleDraft(state.task);
  assert.equal(draft.kind, 'preserve');
  assert.match(ctx.scheduleEditorHtml(state.task), /schedule-agent/);
  assert.equal(draft.model, 'saved-model');
  draft.agentCli = 'codex';
  draft.model = 'scheduled-model';
  await ctx.saveSchedule();
  assert.equal(calls[0].payload.agentCli, 'codex');
  assert.equal(calls[0].payload.model, 'scheduled-model');
  assert.equal(calls[0].payload.schedule.kind, 'preserve');
});

test('manual output is selected by repository and task, including during execution', () => {
  const state = { root: '/a', run: { lines: [{ line: 'A output' }], running: true } };
  let task = { id: 'a' };
  const ctx = vm.createContext({ state, selectedExecutionMachine: () => task, taskIdentity: (t) => t.id });
  vm.runInContext(source.slice(source.indexOf('const taskRunResults ='), source.indexOf('function executionDetailHtml(')), ctx);
  state.run.taskKey = ctx.runTaskKey();
  assert.equal(ctx.selectedTaskRun().lines[0].line, 'A output');
  task = { id: 'b' };
  assert.equal(ctx.selectedTaskRun().lines.length, 0);
  assert.equal(ctx.selectedTaskRun().running, false);
  task = { id: 'a' };
  assert.equal(ctx.selectedTaskRun().running, true);
  state.root = '/b';
  assert.equal(ctx.selectedTaskRun().lines.length, 0);
});
