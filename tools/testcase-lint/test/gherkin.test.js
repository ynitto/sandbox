'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { convert } = require('../src/gherkin');
const { tmp, cli, run, byCode } = require('./helpers');

const source = `# language: ja
@requirement:REQ-12
機能: 送料
  背景:
    前提 配送先は東京都である
  ルール: 通常配送
    背景:
      前提 通常配送を選択している
    シナリオアウトライン: 商品合計<金額>円の送料
      もし 商品合計<金額>円の注文の送料を計算する
      ならば 送料は<送料>円になる
      例:
        | case_id | 金額 | 送料 |
        | SHIP-01 | 4999 | 500 |
        | SHIP-02 | 5000 | 0 |
`;

test('日本語・Rule・二段のBackground・Examplesを展開し、元の行を保持する', () => {
  const { cases, warnings } = convert(source, '../shipping.feature');
  assert.equal(warnings.length, 0);
  assert.deepEqual(cases.map((c) => c.id), ['SHIP-01', 'SHIP-02']);
  assert.deepEqual(cases[0].group, ['送料', '通常配送']);
  assert.deepEqual(cases[0].requirement, ['REQ-12']);
  assert.deepEqual(cases[0].preconditions, ['配送先は東京都である', '通常配送を選択している']);
  assert.deepEqual(cases[1].steps, ['商品合計5000円の注文の送料を計算する']);
  assert.deepEqual(cases[1].expected, ['送料は0円になる']);
  assert.deepEqual(cases[0].conditions, { 金額: '4999' });
  assert.equal(cases[0].lines.line, 14);
  assert.deepEqual(cases[0].lines.fields.preconditions, [5, 8]);
  assert.deepEqual(cases[0].lines.fields.expected, [11]);
});

test('Examplesの行の並べ替えで明示ID・生成IDが変わらない', () => {
  const swapped = source.replace('SHIP-01 | 4999 | 500 |\n        | SHIP-02 | 5000 | 0', 'SHIP-02 | 5000 | 0 |\n        | SHIP-01 | 4999 | 500');
  const semantic = (s) => convert(s, 'shipping.feature').cases.map(({ id, steps, expected }) => ({ id, steps, expected })).sort((a, b) => a.id.localeCompare(b.id));
  assert.deepEqual(semantic(source), semantic(swapped));
  const noIds = (s) => s.replace(/case_id \| /, '').replace(/SHIP-0[12] \| /g, '');
  assert.deepEqual(semantic(noIds(source)), semantic(noIds(swapped)));
  assert.equal(convert(noIds(source), 'shipping.feature').warnings.length, 2);
});

test('And/But・DataTable・DocStringの値と役割を保持する', () => {
  const { cases } = convert(`Feature: f
  @case:TC-01 @REQ-1
  Scenario: s
    Given setup
    And another setup
    When submit
      | field | value |
      | name | A\\|B |
    And upload
      """application/json
      {"name":"value"}
      """
    Then result
    But unchanged
`, 'f.feature');
  assert.deepEqual(cases[0].preconditions, ['setup', 'another setup']);
  assert.match(cases[0].steps[0], /A\|B/);
  assert.match(cases[0].steps[1], /application\/json/);
  assert.deepEqual(cases[0].expected, ['result', 'unchanged']);
});

test('重複ID・空の例のID・不明な役割・構文エラー・空の機能はエラー', () => {
  assert.throws(() => convert(source.replace('SHIP-02', 'SHIP-01'), 'f.feature'), /重複/);
  assert.throws(() => convert(source.replace('SHIP-02', '       '), 'f.feature'), /case_id が空/);
  assert.throws(() => convert('Feature: f\n Scenario: s\n  * something\n', 'f.feature'), /役割/);
  assert.throws(() => convert('Feature: f\n Unexpected:\n', 'f.feature'), /展開できる|expected/);
  assert.throws(() => convert('Feature: f\n', 'f.feature'), /シナリオがありません/);
});

test('別のシナリオが正常でも空のExamplesを黙って捨てない', () => {
  assert.throws(() => convert(`Feature: f
  Scenario: valid
    When act
    Then result
  Scenario Outline: missing <value>
    When input <value>
    Then result
    Examples:
      | value |
`, 'f.feature'), /展開できる例がない/);
});

test('.feature を直接点検し、指摘を元の行に返す', async () => {
  const findings = await run(`Feature: f
  @case:TC-01
  Scenario: s
    When submit
    Then TBD
`, { name: 'f.feature' });
  const [d07] = byCode(findings, 'D07');
  assert.equal(d07.line, 5);
  assert.deepEqual(d07.cases, ['TC-01']);
  assert.match(d07.file, /f\.feature$/);
});

test('ID の無いシナリオは D01、構文の誤りは D00 になる', async () => {
  assert.equal(byCode(await run('Feature: f\n  Scenario: s\n    When a\n    Then b\n', { name: 'f.feature' }), 'D01')[0].line, 2);
  const [d00] = byCode(await run('Feature: f\n  Scenario: s\n    When a\n  Unexpected:\n', { name: 'f.feature' }), 'D00');
  assert.equal(d00.line, 4);
});

test('@temporary タグも暫定テストの候補になる', async () => {
  const findings = await run('Feature: f\n  @case:SHIP-01 @temporary\n  Scenario: s\n    When a\n    Then 送料は0円になる\n', { name: 'f.feature' });
  assert.deepEqual(byCode(findings, 'T01').map((f) => f.cases), [['SHIP-01']]);
});

test('フォルダの下の .feature も読み、JSON のケース一覧には元のファイルとハッシュが出る', async () => {
  const dir = tmp({ 'specs/shipping.feature': source });
  const result = await cli([path.join(dir, 'specs'), '-f', 'json']);
  const data = JSON.parse(result.out);
  assert.deepEqual(data.cases.map((c) => c.id), ['SHIP-01', 'SHIP-02']);
  assert.match(data.cases[0].file, /specs\/shipping\.feature$/);
  assert.match(data.cases[0].input_sha256, /^[a-f0-9]{64}$/);
  assert.ok(fs.existsSync(path.join(dir, 'specs/shipping.feature')));
});
