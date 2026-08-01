'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { parseNoteBlocks, noteBlockFingerprint } = require('../src/renderer/note-blocks');

test('メモを段落と箇条書き項目の選択単位へ分ける', () => {
  const blocks = parseNoteBlocks([
    '# 検収',
    '',
    '証跡が薄いときに気づけるようにする。',
    '',
    '- 差分を見やすくする',
    '  補足も同じ項目に含める',
    '- 再実行の理由を残す',
  ].join('\n'));

  assert.deepStrictEqual(blocks.map(({ heading, text }) => ({ heading, text })), [
    { heading: '検収', text: '証跡が薄いときに気づけるようにする。' },
    { heading: '検収', text: '- 差分を見やすくする\n  補足も同じ項目に含める' },
    { heading: '検収', text: '- 再実行の理由を残す' },
  ]);
});

test('コードフェンスを途中で分割しない', () => {
  const blocks = parseNoteBlocks('## 例\r\n\r\n```js\r\nconst x = 1;\r\n```\r\n\r\n続き');
  assert.deepStrictEqual(blocks.map((block) => block.text), [
    '```js\nconst x = 1;\n```',
    '続き',
  ]);
  assert.ok(blocks.every((block) => block.heading === '例'));
});

test('空メモは選択項目を作らず、指紋は空白差を無視する', () => {
  assert.deepStrictEqual(parseNoteBlocks('  \n\n'), []);
  assert.strictEqual(
    noteBlockFingerprint('見出し', 'a   b'),
    noteBlockFingerprint('見出し', ' a b ')
  );
  assert.match(noteBlockFingerprint(), /^[0-9a-f]{8}$/);
});

test('番号付き項目と閉じていないコードフェンスも本文を失わない', () => {
  const blocks = parseNoteBlocks('### 手順 ###\n1. 最初\n続く段落\n\n~~~\n未完了');
  assert.deepStrictEqual(blocks.map((block) => block.text), [
    '1. 最初',
    '続く段落',
    '~~~\n未完了',
  ]);
  assert.ok(blocks.every((block) => block.heading === '手順'));
});
