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

test('定型の依頼の本文・応答の詳細・スキル一覧の説明・プリセット名は絵文字の途中で切れない', () => {
  const settings = require('../src/main/settings');
  const [quick] = settings.quickRequests([{ text: '😀'.repeat(5000) }]);
  assert.ok(!lone.test(quick.text));
  const response = require('../src/main/response');
  if (typeof response.text === 'function') assert.ok(!lone.test(response.text('😀'.repeat(5000))));
  const reuse = require('../src/shared/reuse');
  const [preset] = reuse.presets([{ name: '😀'.repeat(61) }]);
  assert.ok(!lone.test(preset.name));
  assert.strictEqual(Array.from(preset.name).length, 60);
});

test('取り込んだ会話の出どころの題は絵文字の途中で切れない', () => {
  const { normalizeSession } = require('../src/main/store');
  const out = normalizeSession({ id: 's1', externalOrigin: { key: 'k', title: '😀'.repeat(1999) + '😀😀' } });
  assert.ok(!lone.test(out.externalOrigin.title));
  assert.strictEqual(Array.from(out.externalOrigin.title).length, 2000);
});

test('分岐の依頼本文・記録した操作の対象名・プリセットの CLI 名は絵文字の途中で切れない', () => {
  const fork = require('../src/renderer/forkProtocol');
  const [req] = fork.parseForkRequests('@fork /tmp/x\n' + '😀'.repeat(20000));
  assert.ok(req && !lone.test(req.prompt));
  const reuse = require('../src/shared/reuse');
  const [p] = reuse.presets([{ name: 'a', cli: '😀'.repeat(79) + '😀😀', model: 'm' }]);
  assert.ok(!lone.test(p.cli));
});

test('先頭・末尾の切り出しは絵文字の途中で切れない', () => {
  const { tailChars } = require('../src/main/text');
  assert.ok(!lone.test(tailChars('😀'.repeat(3000), 2001)));
  assert.ok(!lone.test(headChars('😀'.repeat(300), 201)));
});

test('設定の文字列欄・実行履歴のログ末尾は絵文字の途中で切れない', () => {
  const settings = require('../src/main/settings');
  const out = settings.update({ source: '😀'.repeat(499) + '😀😀' });
  assert.ok(!lone.test(out.source));
  assert.strictEqual(Array.from(out.source).length, 500);
  const allocation = require('../src/shared/allocation');
  const a = allocation.normalize({ localModel: '😀'.repeat(159) + '😀😀' });
  assert.ok(!lone.test(a.localModel));
  assert.strictEqual(Array.from(a.localModel).length, 160);
});

test('更新の案内文は絵文字の途中で切れない', () => {
  const update = require('../src/main/update');
  const manifest = update.normalizeManifest({ notes: '😀'.repeat(399) + '😀😀' });
  assert.ok(!lone.test(manifest.notes));
  assert.strictEqual(Array.from(manifest.notes).length, 400);
});

test('受信箱の題・品質評価の抜粋・会話索引の本文は絵文字の途中で切れない', () => {
  const attention = require('../src/main/attention');
  if (typeof attention.text === 'function') assert.ok(!lone.test(attention.text('😀'.repeat(199) + '😀😀')));
  const qe = require('../src/main/quality-evaluation');
  const prepared = qe.prepare({ prompt: '- ' + '😀'.repeat(799) + '😀😀', answer: 'x' });
  for (const c of prepared.criteria) assert.ok(!lone.test(c.text));
});

test('agent-loop の失敗の末尾は絵文字の途中で切れない', async () => {
  const loop = require('../src/main/automation/agent-loop');
  const snapshot = await loop.inspect({
    root: '/project',
    capture: async () => ({ ok: false, status: 1, stderr: '😀'.repeat(4000) }),
  });
  assert.ok(!lone.test(snapshot.error));
  await assert.rejects(() => loop.inspect({ root: '/p', capture: async () => ({ ok: true, stdout: '😀'.repeat(1001) }) }),
    (err) => !lone.test(err.message));
});
