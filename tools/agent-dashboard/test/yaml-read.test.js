'use strict';

// YAML の読み（base/main/yaml.js とその利用側）。追加依存なしで `node test/yaml-read.test.js`。
//
// 移行前は機能ごとの行指向パーサで読んでいて、インラインコメントや列 0 のコメント行で
// 読み落としが出た。**壊れ方が「設定が無いことにされる」＝黙って機能が消える形**だったので、
// ここでコメント入りの実物に近い入力を固定する。

const assert = require('assert');

const { parseYaml, scalarString } = require('../src/base/main/yaml');
const { parseFlatYaml } = require('../src/features/agent-project/main/toolconfig');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('parseYaml は壊れた YAML を例外にせず null で返す', () => {
  assert.strictEqual(parseYaml('key: [壊れた\n'), null);
  assert.strictEqual(parseYaml(''), null);
  assert.deepStrictEqual(parseYaml('a: 1\n'), { a: 1 });
});

test('parseFlatYaml はインラインコメントを値に含めない', () => {
  const got = parseFlatYaml([
    'bus: /home/me/bus   # 共有バス',
    'lock_dir: "/var/lock"  # 引用値の後ろのコメント',
    'node_id: pc-a',
  ].join('\n'));
  assert.deepStrictEqual(got, { bus: '/home/me/bus', lock_dir: '/var/lock', node_id: 'pc-a' });
});

test('parseFlatYaml は引用値の中の # を値の一部として残す', () => {
  assert.deepStrictEqual(parseFlatYaml('note: "a # b"\n'), { note: 'a # b' });
  // 空白の無い `#` は YAML でもコメント開始ではない
  assert.deepStrictEqual(parseFlatYaml('tag: a#b\n'), { tag: 'a#b' });
});

test('parseFlatYaml はトップレベルのスカラだけを文字列で返す', () => {
  const got = parseFlatYaml([
    'bus: /b',
    'count: 5',
    'flag: true',
    'empty:',
    'nested:',
    '  inner: x',
    'list:',
    '  - a',
  ].join('\n'));
  // 呼び出し側は String(values.x) で扱う契約なので、数値・真偽値も文字列で返す。
  assert.deepStrictEqual(got, { bus: '/b', count: '5', flag: 'true' });
});

test('parseFlatYaml は壊れた YAML を空として扱う（画面を落とさない）', () => {
  assert.deepStrictEqual(parseFlatYaml('a: [壊れた\n'), {});
  assert.deepStrictEqual(parseFlatYaml(null), {});
});

test('scalarString はスカラだけを文字列にし、複合値は null', () => {
  assert.strictEqual(scalarString('x'), 'x');
  assert.strictEqual(scalarString(3), '3');
  assert.strictEqual(scalarString(false), 'false');
  assert.strictEqual(scalarString(null), null);
  assert.strictEqual(scalarString({ a: 1 }), null);
  assert.strictEqual(scalarString([1]), null);
});

console.log(`\n${passed} yaml-read tests passed`);
