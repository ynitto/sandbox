'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const selection = require('../src/main/skillSelection');

const catalog = [
  { name: 'ui-designer', description: 'UI UX 画面レイアウトを設計', tags: ['ui', 'ux'], content: 'UI RULES' },
  { name: 'self-checking', description: '成果物を検証', tags: ['review'], content: 'CHECK RULES' },
  { name: 'pdf', description: 'PDFを作成', tags: ['document'], content: 'PDF RULES' },
];

test('自動選択は依頼に合う候補だけを選ぶ', () => {
  const result = selection.select({
    mode: 'auto', text: 'UIの画面レイアウトを改善して',
    candidates: ['ui-designer', 'self-checking', 'pdf'], catalog,
  });
  assert.deepStrictEqual(result.selected.map((item) => item.name), ['ui-designer', 'self-checking']);
  assert.deepStrictEqual(result.omitted, []);
});

test('ネイティブCLIには選択スキルの呼び出しを渡す', () => {
  const selected = selection.select({ mode: 'manual', requested: ['ui-designer'], candidates: ['ui-designer'], catalog });
  const delivery = selection.deliver(selected, { slashNative: true, skillCommandPrefix: '$' });
  assert.deepStrictEqual(delivery.commands, ['$ui-designer']);
  assert.strictEqual(delivery.instruction, '');
  assert.strictEqual(delivery.information[0].title, 'ui-designer');
});

test('依頼で明示したスキルは自動選択の候補外でも優先する', () => {
  const result = selection.select({ mode: 'auto', text: '$pdf でレポートを作って', candidates: ['ui-designer'], catalog });
  assert.strictEqual(result.selected[0].name, 'pdf');
  assert.strictEqual(result.selected[0].reason, '依頼で明示');
});

test('非ネイティブCLIは主スキルを全文で渡し予算外の補助を省略する', () => {
  const selected = selection.select({
    mode: 'manual', requested: ['ui-designer', 'self-checking'], catalog,
  });
  const delivery = selection.deliver(selected, {}, { budgetChars: 10 });
  assert.match(delivery.instruction, /UI RULES/);
  assert.doesNotMatch(delivery.instruction, /CHECK RULES/);
  assert.deepStrictEqual(delivery.omitted, [{ name: 'self-checking', reason: 'コンテキスト予算' }]);
});

test('存在しないスキルの手動選択は実行前に拒否する', () => {
  assert.throws(
    () => selection.select({ mode: 'manual', requested: ['missing'], catalog }),
    (error) => error.code === 'SKILL_NOT_FOUND',
  );
});
