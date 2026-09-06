'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const host = require('../src/renderer/editor-host');

function target({ embedded = false, api = null, parentApi = null } = {}) {
  return {
    api,
    parent: { api: parentApi },
    document: { body: { classList: { contains: (name) => name === 'embedded' && embedded } } },
  };
}

test('standalone は Maker の preload 窓口をそのまま使う', () => {
  const api = { catalog() {} };
  assert.strictEqual(host.resolve(target({ api })), api);
});

test('埋め込み画面は agent-app の automation 窓口だけを使う', () => {
  const automation = { flowList() {} };
  assert.strictEqual(host.resolve(target({ embedded: true, api: { automation } })), automation);
  assert.strictEqual(host.resolve(target({ embedded: true, parentApi: { automation } })), automation);
});

test('埋め込み画面が standalone 窓口へ誤接続しない', () => {
  assert.throws(
    () => host.resolve(target({ embedded: true, api: { catalog() {} } })),
    /接続を初期化できません/,
  );
});
