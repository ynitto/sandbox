'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/automation/flow.js'), 'utf8');

function featureFixture({ saved = false } = {}) {
  const workflow = {
    version: 2, id: 'sample', name: '調査と実装', description: '依頼を調査して実装する',
    nodes: [{ id: 'work', label: '実装', kind: 'work', goal: '{{region}} の {{request}}', deps: [] }],
  };
  const session = {
    workflowId: 'sample', title: workflow.name, status: 'needs-trial',
    understanding: { purpose: '変更依頼を実装する' },
    generations: [{ id: 'generation-1', workflow }], activeGenerationId: 'generation-1',
  };
  const calls = { start: null, view: [] };
  const window = { Publish: { badgeHtml: () => '', cardHtml: () => '' } };
  vm.runInNewContext(source, { window, document: { getElementById: () => null }, clearTimeout, setTimeout });
  const ctx = {
    name: 'ワークフロー', root: () => '/repo', config: () => ({ execution: { tiers: {} } }),
    agents: () => ['codex'], isActive: () => true, refresh: () => {},
    guard: async (_label, action) => action(), toast: () => {}, escape: String,
    dateLabel: () => '', changed: () => {}, teachView: (detail) => calls.view.push(detail),
    bridge: {
      catalog: async () => ({ kinds: [{ kind: 'work', label: '作業' }], patterns: [] }),
      list: async () => saved ? [{ id: 'sample', name: workflow.name, parameterKeys: ['region'] }] : [],
      read: async () => ({ workflow, issues: [] }),
      context: async () => ({ agents: ['codex'], defaults: { agent: 'codex', model: '' }, tools: { agentFlow: { ok: true } }, workspace: { ok: true } }),
      runList: async () => [], runRead: async (_root, runId) => ({ runId, revision: 1, terminal: false }),
      teachingList: async () => saved ? [] : [{ workflowId: 'sample', title: workflow.name, status: 'needs-trial' }],
      teachingRead: async () => session, preview: async () => ({ parameterKeys: ['region'], issues: [] }),
      teachAdopt: async () => session,
      runStart: async (payload) => { calls.start = payload; return { runId: 'run-1' }; },
    },
  };
  return { feature: window.createFlowFeature(ctx), calls };
}

function input(value = '') {
  return {
    value, dataset: {}, handlers: {},
    addEventListener(name, handler) { this.handlers[name] = handler; },
    dispatch(name) { this.handlers[name]?.({ target: this }); },
  };
}

test('workflow teaching exposes test settings and sends the chosen request and inputs to the draft run', async () => {
  const { feature, calls } = featureFixture();
  await feature.activate();
  await feature.select('sample');
  let html = feature.html();
  assert.match(html, /data-flow-teaching-open-test/);
  assert.doesNotMatch(html, /class="execution-card flow-teaching-trial"/);
  const openTest = input();
  feature.bind({
    querySelector: (selector) => selector === '[data-flow-teaching-open-test]' ? openTest : null,
    querySelectorAll: () => [],
  });
  openTest.handlers.click();
  html = feature.html();
  assert.match(html, /class="execution-card flow-teaching-trial"/);
  assert.match(html, /data-flow-teaching-trial-request/);
  assert.match(html, /data-flow-param="region"/);
  assert.match(html, /data-flow-agent/);
  assert.match(html, /data-flow-readonly/);
  assert.match(html, /data-flow-teaching-trial/);
  assert.match(html, /task-detail-shell flow-detail-shell is-editor is-teaching/);
  assert.equal(calls.view.at(-1).card, true);

  const request = input('新しい依頼をテストする');
  const parameter = input('東京');
  parameter.dataset.flowParam = 'region';
  const trial = input();
  const main = {
    querySelector(selector) {
      return ({ '[data-flow-teaching-trial-request]': request, '[data-flow-teaching-trial]': trial })[selector] || null;
    },
    querySelectorAll(selector) { return selector === '[data-flow-param]' ? [parameter] : []; },
  };
  feature.bind(main);
  request.dispatch('input');
  parameter.dispatch('input');
  await trial.handlers.click();

  assert.equal(calls.start.source.type, 'draft');
  assert.equal(calls.start.request, '新しい依頼をテストする');
  assert.equal(calls.start.parameters.region, '東京');
  assert.equal(calls.start.agent, 'codex');
});

test('workflow steps test action opens the saved workflow run settings', async () => {
  const { feature } = featureFixture({ saved: true });
  await feature.activate();
  const steps = input();
  steps.dataset.flowTab = 'steps';
  feature.bind({
    querySelector: () => null,
    querySelectorAll: (selector) => selector === '[data-flow-tab]' ? [steps] : [],
  });
  await steps.handlers.click();
  assert.match(feature.html(), /data-flow-test/);

  const run = input();
  feature.bind({
    querySelector: (selector) => selector === '[data-flow-test]' ? run : null,
    querySelectorAll: () => [],
  });
  await run.handlers.click();
  assert.match(feature.html(), /class="execution-card flow-overview"/);
  assert.match(feature.html(), /data-flow-start/);
});
