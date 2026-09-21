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

test('振り分けの判定が決めたスキルは確率順に採り、文字列の一致の 1 位は使わない', () => {
  const base = { mode: 'auto', text: 'UIの画面レイアウトを改善して', candidates: ['ui-designer', 'self-checking', 'pdf'], catalog };
  const judged = selection.select({ ...base, judged: [{ name: 'pdf', probability: 0.7 }] });
  assert.deepStrictEqual(judged.selected.map((item) => item.name), ['pdf', 'self-checking'], 'ui-designer は判定が採らなかったので拾い直さない');
  assert.strictEqual(judged.selected[0].reason, '判定で選択 0.70');
  const none = selection.select({ ...base, judged: [] });
  assert.deepStrictEqual(none.selected.map((item) => item.name), ['self-checking'], '判定が全部 no でも成果物の検証は付く');
  const explicit = selection.select({ ...base, text: 'ui-designer で画面を改善して', judged: [{ name: 'pdf', probability: 0.9 }] });
  assert.deepStrictEqual(explicit.selected.map((item) => item.name), ['ui-designer', 'pdf', 'self-checking'], '依頼で明示したスキルが先');
  assert.deepStrictEqual(selection.mentioned('pdf を作って', catalog).map((item) => item.name), ['pdf']);
});
