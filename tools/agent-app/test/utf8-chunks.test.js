'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { PassThrough } = require('stream');
const { utf8Stream } = require('../src/main/text');

test('チャンクの境目で割れた日本語・絵文字を文字化けさせない', () => {
  const bytes = Buffer.from('あいう😀えお');
  const stream = utf8Stream(new PassThrough());
  let text = '';
  stream.on('data', (d) => { text += d; });
  for (let i = 0; i < bytes.length; i += 2) stream.write(bytes.subarray(i, i + 2));
  stream.end();
  return new Promise((resolve) => stream.on('end', () => { assert.equal(text, 'あいう😀えお'); resolve(); }));
});

test('setEncoding を持たない相手はそのまま返す', () => {
  const fake = { on() {} };
  assert.equal(utf8Stream(fake), fake);
});

test('バイト数で切った断片は割れた文字を残さない', () => {
  const { trimUtf8 } = require('../src/main/text');
  const buf = Buffer.from('あい😀う');
  for (let n = 0; n <= buf.length; n += 1) {
    assert.ok(!trimUtf8(buf.subarray(0, n), { cutEnd: true }).toString('utf8').includes('�'), `head ${n}`);
    assert.ok(!trimUtf8(buf.subarray(n), { cutStart: true }).toString('utf8').includes('�'), `tail ${n}`);
  }
  assert.strictEqual(trimUtf8(buf, { cutEnd: true }).toString('utf8'), 'あい😀う');
});
