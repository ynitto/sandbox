'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flowSettings = require('../src/main/automation/flow-settings');

function dirs() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-flow-settings-'));
  const root = path.join(base, 'repo');
  const home = path.join(base, 'home');
  fs.mkdirSync(root, { recursive: true });
  fs.mkdirSync(home, { recursive: true });
  return { root, home };
}

test('設定ファイルが無ければ既定を返し、保存するとホームに作る（既定と同じ値は書かない）', () => {
  const { root, home } = dirs();
  const read = flowSettings.read(root, { home });
  assert.strictEqual(read.exists, false);
  assert.strictEqual(read.file, path.join(home, '.agents', 'agent-flow.yaml'));
  assert.strictEqual(read.values.size, 'small');
  assert.strictEqual(read.values.review, 'auto');
  const saved = flowSettings.save(root, { size: 'medium', workers: 2, plan_gate: true }, { home });
  assert.strictEqual(saved.values.size, 'medium');
  assert.strictEqual(saved.values.plan_gate, true);
  const text = fs.readFileSync(saved.file, 'utf8');
  assert.match(text, /size: medium/);
  assert.doesNotMatch(text, /workers/);
});

test('agent-flow と同じ順で探し、リポジトリの設定を優先する', () => {
  const { root, home } = dirs();
  fs.mkdirSync(path.join(home, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(home, '.agents', 'agent-flow.yaml'), 'size: large\n');
  assert.strictEqual(flowSettings.read(root, { home }).values.size, 'large');
  fs.mkdirSync(path.join(root, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(root, '.agents', 'agent-flow.yaml'), 'size: medium\n');
  const found = flowSettings.read(root, { home });
  assert.strictEqual(found.values.size, 'medium');
  assert.strictEqual(found.home, false);
  fs.writeFileSync(path.join(root, 'agent-flow.json'), JSON.stringify({ size: 'unrestricted' }));
  assert.strictEqual(flowSettings.read(root, { home }).values.size, 'unrestricted');
});

test('保存はコメントとほかのキーを残し、値を語彙へ丸める', () => {
  const { root, home } = dirs();
  const file = path.join(root, 'agent-flow.yaml');
  fs.writeFileSync(file, '# バス\nbus: ./bus\ngranularity: fine   # 細かさ\nreview: true\n');
  const saved = flowSettings.save(root, { granularity: 'coarse', review: 'false', max_retries: 99, size: 'huge' }, { home });
  const text = fs.readFileSync(file, 'utf8');
  assert.match(text, /# バス/);
  assert.match(text, /bus: \.\/bus/);
  assert.match(text, /granularity: coarse/);
  assert.strictEqual(saved.values.review, false);
  assert.strictEqual(saved.values.max_retries, 20);
  assert.strictEqual(saved.values.size, 'small');
});

test('JSON の設定もキーを足して書き戻す', () => {
  const { root, home } = dirs();
  const file = path.join(root, 'agent-flow.json');
  fs.writeFileSync(file, JSON.stringify({ bus: './bus' }));
  flowSettings.save(root, { size: 'large' }, { home });
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { bus: './bus', size: 'large' });
});

test('読めない設定は理由を添えて断る', () => {
  const { root, home } = dirs();
  fs.writeFileSync(path.join(root, 'agent-flow.yaml'), 'size: [\n');
  assert.throws(() => flowSettings.read(root, { home }), (err) => err.code === 'settings-unreadable');
});
