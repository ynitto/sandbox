'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const response = require('../src/main/response');

test('Codex JSONLのreasoning・command・file changeを共通レスポンスへ変換する', () => {
  const collector = response.createCollector('codex');
  const progress = collector.push(JSON.stringify({ type: 'item.completed', item: {
    type: 'reasoning', text: '関連コードを確認した',
  } }));
  const command = collector.push(JSON.stringify({ type: 'item.completed', item: {
    type: 'command_execution', command: 'npm test', exit_code: 0, status: 'completed',
  } }));
  collector.push(JSON.stringify({ type: 'item.completed', item: {
    type: 'file_change', changes: [{ path: 'src/app.js', kind: 'update' }], status: 'completed',
  } }));
  assert.deepStrictEqual(collector.parts(), {
    thinking: [{ text: '関連コードを確認した', status: 'done' }],
    information: [
      { type: 'command', title: 'npm test', status: 'success', detail: '' },
      { type: 'file', title: 'src/app.js', status: 'success', action: 'modified' },
    ],
  });
  assert.deepStrictEqual(progress, { thinking: [{ text: '関連コードを確認した', status: 'done' }], information: [] });
  assert.deepStrictEqual(command, { thinking: [], information: [{ type: 'command', title: 'npm test', status: 'success', detail: '' }] });
  assert.deepStrictEqual(collector.push('not json'), { thinking: [], information: [] });
});

test('AiderのTHINKINGとANSWERを思考・回答へ分離する', () => {
  const raw = `@agent-usage tokens_in=3534 tokens_out=397

Aider v0.86.2 Model: ollama_chat/gemma4:e4b with whole edit format

--------------

► **THINKING**

The user supplied a skill guide.

Keep this divider:
---
inside the quoted prompt.

Plan:
1. Acknowledge the rules.
2. Respond tersely.

------------

► **ANSWER**

Rules set. Protocol understood. Ready.

Tokens: 3.5k sent, 397 received.`;
  assert.deepStrictEqual(response.parseTranscript('aider', raw), {
    text: 'Rules set. Protocol understood. Ready.',
    thinking: [{
      text: 'The user supplied a skill guide.\n\nKeep this divider:\n---\ninside the quoted prompt.\n\nPlan:\n1. Acknowledge the rules.\n2. Respond tersely.',
      status: 'done',
    }],
  });
  assert.deepStrictEqual(response.parseTranscript('claude', raw), { text: raw, thinking: [] });
  assert.deepStrictEqual(response.parseTranscript('aider', 'plain answer'), { text: 'plain answer', thinking: [] });
});

test('Copilotのスキル・ツール表示を回答から分離する', () => {
  const raw = `● skill(ponytail)

● Fetching web content https://wttr.in/Tokyo
● 東京の今日：

  • 天気：雨`;
  assert.deepStrictEqual(response.parseTranscript('copilot', raw), {
    text: '● 東京の今日：\n\n  • 天気：雨',
    thinking: [{ text: 'Fetching web content https://wttr.in/Tokyo', status: 'done' }],
  });
});
