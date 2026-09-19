'use strict';

// 設定 > 実行制御「遷移や振り分けの判定」。値は agent-herd の設定ファイルにあり、
// このアプリは `agent-herd config` の出入りの形だけを持つ。

const { test } = require('node:test');
const assert = require('node:assert');
const judge = require('../src/main/judgeSetting');

test('agent-herd config --json の姿を画面の 3 値へ写す', () => {
  assert.deepStrictEqual(judge.parseStatus('{"path": null, "judge": {"mode": "auto", "model": null}}'),
    { mode: 'auto', model: '' });
  assert.deepStrictEqual(judge.parseStatus('{"judge": {"mode": "pinned", "model": "gemma4:e4b"}}'),
    { mode: 'model', model: 'gemma4:e4b' });
  assert.deepStrictEqual(judge.parseStatus('{"judge": {"mode": "off"}}'), { mode: 'off', model: '' });
  assert.throws(() => judge.parseStatus('not json'), /読み取れません/);
});

test('画面の値を config set judge.model の 1 語へ。モデル無しの「指定」は auto に倒す', () => {
  assert.deepStrictEqual(judge.setArgs({ mode: 'model', model: ' gemma4:12b ' }), ['config', 'set', 'judge.model', 'gemma4:12b']);
  assert.deepStrictEqual(judge.setArgs({ mode: 'off' }), ['config', 'set', 'judge.model', 'off']);
  assert.deepStrictEqual(judge.setArgs({ mode: 'auto', model: 'ignored' }), ['config', 'set', 'judge.model', 'auto']);
  assert.deepStrictEqual(judge.setArgs({ mode: 'model', model: '' }), ['config', 'set', 'judge.model', 'auto']);
  assert.deepStrictEqual(judge.setArgs({ mode: 'bogus' }), ['config', 'set', 'judge.model', 'auto']);
});

test('read: agent-herd が無ければ available=false で既定値（画面は行を薄くするだけで落ちない）', async () => {
  const missing = await judge.read({ capture: async () => ({ ok: false, status: -1, stdout: '', stderr: '', error: 'spawn agent-herd ENOENT' }) });
  assert.strictEqual(missing.available, false);
  assert.deepStrictEqual(missing.value, { mode: 'auto', model: '' });
  assert.match(missing.error, /ENOENT/);
  const calls = [];
  const present = await judge.read({ capture: async (cmd, args) => { calls.push([cmd, args]); return { ok: true, status: 0, stdout: '{"judge": {"mode": "pinned", "model": "gemma4:e4b"}}\n', stderr: '' }; } });
  assert.deepStrictEqual(calls, [['agent-herd', ['config', '--json']]]);
  assert.deepStrictEqual(present, { available: true, value: { mode: 'model', model: 'gemma4:e4b' }, error: '' });
});

test('write: config set に頼み、失敗は理由つきで投げる', async () => {
  const calls = [];
  const saved = await judge.write({ capture: async (cmd, args) => { calls.push([cmd, args]); return { ok: true, status: 0, stdout: '{}', stderr: '' }; }, value: { mode: 'model', model: 'gemma4:e4b' } });
  assert.deepStrictEqual(calls, [['agent-herd', ['config', 'set', 'judge.model', 'gemma4:e4b']]]);
  assert.deepStrictEqual(saved, { mode: 'model', model: 'gemma4:e4b' });
  await assert.rejects(
    judge.write({ capture: async () => ({ ok: false, status: 2, stdout: '', stderr: '[agent-error:env] agent-herd: 未知の設定', error: '' }), value: { mode: 'off' } }),
    /未知の設定/);
});
