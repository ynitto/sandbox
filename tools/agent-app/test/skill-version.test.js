'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { readVersion, compareVersions } = require('../src/main/skillVersion');

test('metadata.version と従来の version を読み、本文の記載は拾わない', () => {
  assert.equal(readVersion('---\nmetadata:\n  version: "1.10.0"\n---\n# skill'), '1.10.0');
  assert.equal(readVersion('---\r\nversion: 1.2.3\r\n---\r\n'), '1.2.3');
  assert.equal(readVersion('---\nmetadata: {version: 2.0.0}\n---\n'), '2.0.0');
  assert.equal(readVersion('---\nmetadata:\n  version: 1.10\n---\n'), '1.10');
  assert.equal(readVersion('# skill\nversion: 9.0.0'), '');
  assert.equal(readVersion('---\nmetadata: [\n---\n'), '');
});

test('数値・先行版・ビルド情報を比較し、版不明をゼロ扱いしない', () => {
  for (const [a, b, expected] of [
    ['1.10.0', '1.9.9', 1], ['2.0', '2.0.0', 0], ['v1.2.3', '1.2.3', 0],
    ['1.0.0-rc.10', '1.0.0-rc.2', 1], ['1.0.0-rc.1', '1.0.0', -1],
    ['1.0.0+build.1', '1.0.0+build.2', 0], ['', '1.0.0', null], ['latest', '1.0.0', null],
  ]) assert.equal(compareVersions(a, b), expected, `${a} / ${b}`);
});
