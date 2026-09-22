"use strict";
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const agentCli = require('../src/main/agentCli');
const herd = require('../src/main/herd');

function functionSource(file, start, end) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
const runSource = functionSource('src/main/ipc.js', 'async function runTmux(', '// いま応答中の会話');

for (const cli of ['ollama', 'aider', 'claude']) {
  for (const resumed of [false, true]) test(`tmux follow-up preserves context: ${cli}, reused=${resumed}`, async () => {
    const messages = [];
    const sent = [];
    const launch = { cli, model: 'test-model', readonly: true, autoApprove: false };
    const conv = { launch, seen: 0, resumed, phase: 'ready', waitReady: async () => {},
      send: async (prompt, done) => {
        sent.push(prompt);
        done({ role: 'assistant', text: sent.length === 1 ? 'どこの天気ですか？' : '東京の天気ですね。' });
      } };
    const context = vm.createContext({
      userData: () => '', conversations: new Map([['weather', conv]]),
      sameLaunch: (a, b) => JSON.stringify(a) === JSON.stringify(b),
      store: { readSession: () => ({ messages }), appendMessage: (_ud, _id, m) => { messages.push(m); return { messages }; }, setCliEntry: () => {} },
      agentCli, herd, response: { parseTranscript: (_cli, text) => ({ text, thinking: [] }) },
      audit: { feedTurn: () => {} }, evaluator: null,
    });
    vm.runInContext(runSource, context);
    for (const text of ['今日の天気は？', '東京', '明日は？']) {
      await context.runTmux('weather', { ...launch, text, prompt: text, spec: agentCli.load(cli, path.resolve(__dirname, '../../..')), slash: '/ask', setupInformation: [], release: () => {} }, () => {});
    }
    assert.equal(messages.length, 6);
    assert.match(sent[1], /^\/ask\n/);
    if (cli === 'claude') {
      assert.equal(sent[1], '/ask\n東京', 'stateful CLI keeps its own context without duplicate replay');
    } else {
      for (const prompt of sent.slice(1)) {
        assert.match(prompt, /今日の天気は？/);
        assert.match(prompt, /どこの天気ですか？/);
        assert.equal(prompt.split('今日の天気は？').length - 1, 1, 'history is replayed once');
      }
      assert.match(sent[2], /東京の天気ですね。/);
    }
  });
}

const sendSource = functionSource('src/renderer/renderer.js', 'async function sendPrompt()', 'async function sendPromptRequest()');
for (const existing of [false, true]) test(`preparation covers only a new conversation: existing=${existing}`, async () => {
  const state = { current: existing ? { id: 'weather', messages: [{ role: 'user', text: '今日の天気は？' }, { role: 'assistant', text: 'どこの天気ですか？' }] } : null,
    repo: '/repo', attachments: [], running: new Set(), preparation: null };
  let observed;
  const context = vm.createContext({ state, $: () => ({ value: '東京' }), shareWaiting: () => null,
    renderHeader: () => {}, Term: { refit: () => {} },
    sendPromptRequest: async () => { observed = state.preparation; },
  });
  vm.runInContext(sendSource, context);
  await context.sendPrompt();
  assert.equal(!!observed, !existing);
  assert.equal(state.preparation, null);
});
