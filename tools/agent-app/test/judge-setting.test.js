'use strict';

// 設定 > 実行制御「遷移や振り分けの判定」。値は agent-herd の設定ファイルにあり、
// このアプリは `agent-herd config` の出入りの形だけを持つ。

const { test } = require('node:test');
const assert = require('node:assert');
const judge = require('../src/main/judgeSetting');

test('agent-herd config --json の姿を画面の値へ写す', () => {
  assert.deepStrictEqual(judge.parseStatus('{"path": null, "judge": {"mode": "auto", "model": null}}'),
    { mode: 'auto', model: '', keepMinutes: '' });
  assert.deepStrictEqual(judge.parseStatus('{"judge": {"mode": "pinned", "model": "gemma4:e4b"}}'),
    { mode: 'model', model: 'gemma4:e4b', keepMinutes: '' });
  assert.deepStrictEqual(judge.parseStatus('{"judge": {"mode": "off"}}'), { mode: 'off', model: '', keepMinutes: '' });
  assert.throws(() => judge.parseStatus('not json'), /読み取れません/);
});

test('残す時間は分で扱い、分で書けない値は空にして触らない', () => {
  assert.strictEqual(judge.parseStatus('{"keep_alive": "30m", "judge": {"mode": "auto"}}').keepMinutes, 30);
  assert.strictEqual(judge.parseStatus('{"keep_alive": "3600", "judge": {"mode": "auto"}}').keepMinutes, 60, '単位なしは秒');
  assert.strictEqual(judge.parseStatus('{"keep_alive": "2h", "judge": {"mode": "auto"}}').keepMinutes, 120);
  assert.strictEqual(judge.parseStatus('{"keep_alive": "0", "judge": {"mode": "auto"}}').keepMinutes, 0);
  assert.strictEqual(judge.parseStatus('{"keep_alive": "-1", "judge": {"mode": "auto"}}').keepMinutes, '', '常駐は分では書けない');
  assert.strictEqual(judge.parseStatus('{"keep_alive": "500ms", "judge": {"mode": "auto"}}').keepMinutes, '');
  assert.strictEqual(judge.parseStatus('{"keep_alive": "9999m", "judge": {"mode": "auto"}}').keepMinutes, '', '上限を超える値も画面では持たない');
  assert.deepStrictEqual(judge.keepArgs({ mode: 'auto', keepMinutes: 30 }), ['config', 'set', 'judge.keep_alive', '30m']);
  assert.deepStrictEqual(judge.keepArgs({ mode: 'auto', keepMinutes: 0 }), ['config', 'set', 'judge.keep_alive', '0m'], '0 は即解放');
  assert.deepStrictEqual(judge.keepArgs({ mode: 'auto', keepMinutes: '' }), ['config', 'set', 'judge.keep_alive', ''], '空は既定へ戻す');
  assert.deepStrictEqual(judge.keepArgs({ mode: 'auto', keepMinutes: -5 }), ['config', 'set', 'judge.keep_alive', '']);
  assert.deepStrictEqual(judge.keepArgs({ mode: 'auto', keepMinutes: 99999 }), ['config', 'set', 'judge.keep_alive', `${judge.MAX_KEEP_MINUTES}m`]);
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
  assert.deepStrictEqual(missing.value, { mode: 'auto', model: '', keepMinutes: '' });
  assert.match(missing.error, /ENOENT/);
  const calls = [];
  const present = await judge.read({ capture: async (cmd, args) => { calls.push([cmd, args]); return { ok: true, status: 0, stdout: '{"judge": {"mode": "pinned", "model": "gemma4:e4b"}}\n', stderr: '' }; } });
  assert.deepStrictEqual(calls, [['agent-herd', ['config', '--json']]]);
  assert.deepStrictEqual(present, { available: true, value: { mode: 'model', model: 'gemma4:e4b', keepMinutes: '' }, error: '' });
});

test('write: config set に頼み、失敗は理由つきで投げる', async () => {
  const calls = [];
  const capture = async (cmd, args) => { calls.push([cmd, args]); return { ok: true, status: 0, stdout: '{}', stderr: '' }; };
  const saved = await judge.write({ capture, value: { mode: 'model', model: 'gemma4:e4b', keepMinutes: 30 } });
  assert.deepStrictEqual(calls, [
    ['agent-herd', ['config', 'set', 'judge.model', 'gemma4:e4b']],
    ['agent-herd', ['config', 'set', 'judge.keep_alive', '30m']],
  ]);
  assert.deepStrictEqual(saved, { mode: 'model', model: 'gemma4:e4b', keepMinutes: 30 });
  calls.length = 0;
  await judge.write({ capture, value: { mode: 'off' }, keepAlive: false });
  assert.deepStrictEqual(calls, [['agent-herd', ['config', 'set', 'judge.model', 'off']]], '残す時間を変えていなければ触らない');
  await assert.rejects(
    judge.write({ capture: async () => ({ ok: false, status: 2, stdout: '', stderr: '[agent-error:env] agent-herd: 未知の設定', error: '' }), value: { mode: 'off' } }),
    /未知の設定/);
});
