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
