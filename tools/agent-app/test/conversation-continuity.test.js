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
      await context.runTmux('weather', { ...launch, text, prompt: text, bare: text, spec: agentCli.load(cli, path.resolve(__dirname, '../../..')), slash: '/ask', setupInformation: [], release: () => {} }, () => {});
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

// 共通指示（prompt）は CLI の文脈がまだ持っていないときだけ送り、2 ターン目からは素の本文（bare）。
// 文脈を保てない共通 TUI（ollama）は毎回。文脈を引き継いで開き直した会話（instructed）は初回から素の本文。
for (const [cli, instructed, expectBlocks] of [['claude', false, [true, false, false]], ['claude', true, [false, false, false]], ['ollama', false, [true, true, true]]]) {
  test(`common instructions once per CLI context: ${cli}, instructed=${instructed}`, async () => {
    const messages = [];
    const sent = [];
    const launch = { cli, model: 'test-model', readonly: false, autoApprove: true };
    const conv = { launch, seen: 0, resumed: instructed, instructed, phase: 'ready', waitReady: async () => {},
      send: async (prompt, done) => { sent.push(prompt); done({ role: 'assistant', text: '了解' }); } };
    const context = vm.createContext({
      userData: () => '', conversations: new Map([['job', conv]]),
      sameLaunch: (a, b) => JSON.stringify(a) === JSON.stringify(b),
      store: { readSession: () => ({ messages }), appendMessage: (_ud, _id, m) => { messages.push(m); return { messages }; }, setCliEntry: () => {} },
      agentCli, herd, response: { parseTranscript: (_cli, text) => ({ text, thinking: [] }) },
      audit: { feedTurn: () => {} }, evaluator: null,
    });
    vm.runInContext(runSource, context);
    for (const text of ['一', '二', '三']) {
      await context.runTmux('job', { ...launch, text, prompt: `## 共通指示\n丁寧に\n\n## 今回の依頼\n${text}`, bare: text,
        spec: agentCli.load(cli, path.resolve(__dirname, '../../..')), slash: '', setupInformation: [], release: () => {} }, () => {});
    }
    assert.deepEqual(sent.map((prompt) => /## 共通指示/.test(prompt)), expectBlocks);
    assert.ok(sent.every((prompt) => /三|二|一/.test(prompt)), '本文は毎回届く');
  });
}

// 教示画面を開き直したときの再開文は、この CLI の文脈が会話を持っていれば送らない。
// 起動時に ID を持てない CLI（kiro）は resumed が立たないので、やり取りを通したか（seen）で見る。
test('reopening a live teaching conversation does not resend the resume prompt', async () => {
  const messages = [];
  const sent = [];
  const launch = { cli: 'kiro', model: '', readonly: false, autoApprove: true };
  const conv = { launch, seen: 0, resumed: false, phase: 'ready', waitReady: async () => {},
    send: async (prompt, done) => { sent.push(prompt); done({ role: 'assistant', text: '了解' }); } };
  const context = vm.createContext({
    userData: () => '', conversations: new Map([['teach', conv]]),
    sameLaunch: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    store: { readSession: () => ({ messages }), appendMessage: (_ud, _id, m) => { messages.push(m); return { messages }; }, setCliEntry: () => {} },
    agentCli, herd, response: { parseTranscript: (_cli, text) => ({ text, thinking: [] }) },
    audit: { feedTurn: () => {} }, evaluator: null,
  });
  vm.runInContext(runSource, context);
  const spec = agentCli.load('kiro', path.resolve(__dirname, '../../..'));
  const turn = (text, extra = {}) => ({ ...launch, text, prompt: text, bare: text, spec, slash: '', setupInformation: [], release: () => {}, ...extra });
  // 起動直後の初回: 再開文をそのまま送る（文脈が無い）
  const first = await context.runTmux('teach', turn('このタスクの編集を開始します', { resumeContext: '' }), () => {});
  assert.equal(first.started, true);
  // 開き直し: 文脈があるので何も送らない
  const reopened = await context.runTmux('teach', turn('このタスクの編集を開始します', { resumeContext: '' }), () => {});
  assert.equal(reopened.started, false);
  // 編集対象を選んで開き直し: 対象の 1 行だけ送る
  await context.runTmux('teach', turn('今回の編集対象: 工程2', { resumeContext: '今回の編集対象: 工程2' }), () => {});
  assert.deepEqual(sent, ['このタスクの編集を開始します', '今回の編集対象: 工程2']);
});

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
