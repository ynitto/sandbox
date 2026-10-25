'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('node:child_process');
const { convert } = require('../src/convert');

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

test('日本語・Rule・二段のBackground・Examplesを展開し入力元を保持する', () => {
  const { cases, warnings } = convert(source, '../shipping.feature');
  assert.equal(warnings.length, 0);
  assert.deepEqual(cases.map((c) => c.id), ['SHIP-01', 'SHIP-02']);
  assert.deepEqual(cases[0].group, ['送料', '通常配送']);
  assert.deepEqual(cases[0].requirement, ['REQ-12']);
  assert.deepEqual(cases[0].preconditions, ['配送先は東京都である', '通常配送を選択している']);
  assert.deepEqual(cases[1].steps, ['商品合計5000円の注文の送料を計算する']);
  assert.deepEqual(cases[1].expected, ['送料は0円になる']);
  assert.deepEqual(cases[0].conditions, { 金額: '4999' });
  assert.equal(cases[0].origin.line, 14);
  assert.deepEqual(cases[0].origin.fields.preconditions, [5, 8]);
  assert.deepEqual(cases[0].origin.fields.expected, [11]);
  assert.match(cases[0].origin.input_sha256, /^[a-f0-9]{64}$/);
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

test('CLIは出力先からの相対パスを書き、失敗時に既存出力を壊さない', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gherkin-cases-'));
  try {
    fs.writeFileSync(path.join(dir, 'shipping.feature'), source);
    const cli = path.resolve(__dirname, '../bin/gherkin-cases.js');
    const output = path.join(dir, 'generated', 'cases.json');
    let result = spawnSync(process.execPath, [cli, 'shipping.feature', '-o', output], { cwd: dir, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(fs.readFileSync(output, 'utf8')).cases[0].origin.file, '../shipping.feature');
    const previous = fs.readFileSync(output, 'utf8');
    fs.writeFileSync(path.join(dir, 'shipping.feature'), 'Feature: empty\n');
    result = spawnSync(process.execPath, [cli, 'shipping.feature', '-o', output], { cwd: dir, encoding: 'utf8' });
    assert.equal(result.status, 2);
    assert.equal(fs.readFileSync(output, 'utf8'), previous);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
