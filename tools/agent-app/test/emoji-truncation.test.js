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
