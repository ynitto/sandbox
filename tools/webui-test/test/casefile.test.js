'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { normalize, loadFile } = require('../src/casefile');

test('同梱の例は書式に合う', () => {
  const { suite, errors } = loadFile(path.join(__dirname, '..', 'examples', 'login.yaml'));
  assert.deepStrictEqual(errors, []);
  assert.strictEqual(suite.cases.length, 4);
  assert.strictEqual(suite.screenshot, 'step');
});

test('既定値を補う', () => {
  const { suite } = normalize({ cases: [{ id: 1, title: 'a', steps: [{ goto: '/' }] }] });
  assert.strictEqual(suite.browser, 'chromium');
  assert.deepStrictEqual(suite.viewport, { width: 1280, height: 800 });
  assert.strictEqual(suite.cases[0].id, '1');
});

test('誤りを場所つきで挙げる', () => {
  const { suite, errors } = normalize({
    cases: [
      { id: 'A', title: 'x', steps: [{ tap: 'x' }] },
      { id: 'A', steps: [{ click: { role: 'button', css: '#b' } }] },
      { id: 'B', title: 'y', steps: [{ expect: { text: 'hi' } }, { fill: { target: 'input' } }] },
    ],
  });
  assert.strictEqual(suite, null);
  const all = errors.join('\n');
  assert.match(all, /cases\[0\]\.steps\[0\]: 知らないキー「tap」/);
  assert.match(all, /cases\[1\]: id「A」が重複/);
  assert.match(all, /cases\[1\]: title が要ります/);
  assert.match(all, /ちょうど 1 つ/);
  assert.match(all, /text には target が要ります/);
  assert.match(all, /fill: value が要ります/);
});

test('ケースが無いと落とす', () => {
  assert.match(normalize({ suite: 'x' }).errors.join(), /cases/);
  assert.match(normalize([]).errors.join(), /トップレベル/);
});
