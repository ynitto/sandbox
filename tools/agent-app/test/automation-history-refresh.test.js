'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/renderer/automation/renderer.js'), 'utf8');

test('履歴タブを開くと定期実行の最新履歴を取得する', async () => {
  let click, refreshes = 0;
  const state = { view: 'home', execution: { detailTab: 'overview' }, aiReview: {} };
  const ctx = vm.createContext({ state, selectedExecutionMachine: () => ({ kind: 'command' }),
    cancelAi() {}, render() {}, refreshExecutionSnapshot: async () => { refreshes++; } });
  vm.runInContext(source.slice(source.indexOf('function bindTaskDetailTabs('), source.indexOf('function goRun(')), ctx);
  ctx.bindTaskDetailTabs({ querySelectorAll: () => [{ dataset: { taskTab: 'history' }, addEventListener: (_event, fn) => { click = fn; } }] });
  await click();
  assert.equal(refreshes, 1);
});

test('表示中の履歴は再取得し、非表示・通信中は重複取得しない', async () => {
  let refreshes = 0;
  const state = { root: '/repo', view: 'home', homeTab: 'run', execution: { detailTab: 'history' } };
  const document = { hidden: false };
  const ctx = vm.createContext({ state, document, workbenchHost: null,
    refreshExecutionSnapshot: async () => { refreshes++; } });
  vm.runInContext(source.slice(source.indexOf('async function refreshVisibleHistory('), source.indexOf('// 前回の手動実行')), ctx);
  await ctx.refreshVisibleHistory();
  assert.equal(refreshes, 1);
  state.execution.loading = true;
  await ctx.refreshVisibleHistory();
  state.execution.loading = false;
  document.hidden = true;
  await ctx.refreshVisibleHistory();
  document.hidden = false;
  state.homeTab = 'teach';
  await ctx.refreshVisibleHistory();
  assert.equal(refreshes, 1);
});
