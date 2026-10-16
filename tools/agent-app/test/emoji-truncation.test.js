'use strict';

// 文字数で切る箇所が、絵文字（サロゲートペア）の途中で切らないこと。

const { test } = require('node:test');
const assert = require('node:assert');
const ProjectPath = require('../src/shared/projectPath');

test('プロジェクトのフォルダ名は絵文字の途中で切れない', () => {
  const name = folderOf('😀'.repeat(59) + '😀😀');
  assert.ok(!/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/.test(name));
  assert.strictEqual(Array.from(name).length, 60);
});

function folderOf(s) { return ProjectPath.folderName(s); }

const { headChars } = require('../src/main/text');
const lone = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;

test('共有の添付名は絵文字の途中で切れない', () => {
  const { safeName } = require('../src/main/share/participant');
  const name = safeName('😀'.repeat(119) + '😀😀.txt');
  assert.ok(!lone.test(name));
  assert.strictEqual(Array.from(name).length, 120);
});

test('定型化の理由は絵文字の途中で切れない', () => {
  const routine = require('../src/main/automation/routine');
  const out = routine.parse(JSON.stringify({ kind: KIND(routine), reason: '😀'.repeat(999) + '😀😀', purpose: 'x' }));
  assert.ok(!lone.test(out.reason));
});

function KIND(routine) { return routine.KINDS[0]; }
