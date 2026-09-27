'use strict';

// 分担の形（shared/flowShapes）。作成画面・依頼から実行・AI への最初の依頼が同じ 1 つを見ることと、
// 名指しする標準パターンが agent-flow に実在することを縛る。
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const shapes = require('../src/shared/flowShapes');
const prompt = require('../src/main/automation/flow-teaching-prompt');
const model = require('../src/main/automation/flow-teaching-model');

test('3 つの形は README と同じ言葉で、agent-flow の標準パターンを名指しする', () => {
  assert.deepStrictEqual(shapes.SHAPES.map((s) => [s.id, s.label]), [
    ['verify', '別の目で確かめる'], ['compare', '並べて比べる'], ['split', '分けて広く進める'],
  ]);
  const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
  for (const shape of shapes.SHAPES) assert.ok(readme.includes(shape.label), `README に「${shape.label}」が無い`);
  const catalog = fs.readFileSync(path.join(__dirname, '..', '..', 'agent-flow', 'agent_flow', 'patterns.py'), 'utf8');
  const names = new Set([...catalog.slice(catalog.indexOf('PATTERNS = {'), catalog.indexOf('}', catalog.indexOf('PATTERNS = {'))).matchAll(/"([a-z-]+)":/g)].map((m) => m[1]));
  for (const shape of shapes.SHAPES) assert.ok(names.has(shape.pattern), `agent-flow に無いパターン: ${shape.pattern}`);
  assert.strictEqual(shapes.normalize('verify'), 'verify');
  assert.strictEqual(shapes.normalize('swarm'), '', '知らない値はおまかせ');
  assert.strictEqual(shapes.AUTO.id, '');
});

test('作成で選んだ形は下書きに残り、最初の依頼の末尾に 1 行足す（変更の会話とおまかせには足さない）', () => {
  const session = model.normalizeSession(model.createSession({ workflowId: 'fix', purpose: '不具合を直す', shape: 'verify' }));
  assert.strictEqual(session.shape, 'verify');
  assert.strictEqual(model.normalizeSession({ shape: 'bogus' }).shape, '');
  const withShape = prompt.prompt({ id: 'fix', purpose: '不具合を直す', shape: 'verify' });
  assert.ok(withShape.endsWith(shapes.find('verify').instruction), '目的の後に形の指示を置く');
  assert.ok(withShape.indexOf('不具合を直す') < withShape.indexOf('別の目で確かめる'));
  assert.ok(!prompt.prompt({ id: 'fix', purpose: '不具合を直す' }).includes('分担の形'), 'おまかせは何も足さない');
  assert.ok(!prompt.prompt({ id: 'fix', existing: true, shape: 'verify' }).includes('分担の形'), '既存の定義を変える会話には足さない');
  for (const shape of shapes.SHAPES) {
    // 定義に残らない項目（工程ごとの AI 指定・decision）を書かせない。flow-model が保存しないので黙って落ちる
    assert.ok(!/\btier\b|\bagent\b|decision/.test(shape.instruction), `${shape.id} の指示が保存されない項目を求めている`);
  }
});
