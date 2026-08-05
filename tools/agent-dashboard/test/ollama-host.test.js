'use strict';

// ローカルモデル（ollama）の接続先の読み書き。
// - 読み: 実行側のノード宣言（host.yaml）の `ollama.host` が正（画面側に env を持たない）
// - 書き: `host:` 1 行だけの外科的書換（コメント・順序・他ブロックを壊さない）
// - 宣言ファイルが無い端末では**作らない**（空の host.yaml がノード宣言に化けるのを防ぐ）

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ollama = require('../src/features/orchestration/main/ollama');
const { engineConfig } = require('./helpers/engine-status');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function hostConfig(text) {
  const cfg = engineConfig([]);
  const file = path.join(cfg.engine.home, 'agent-project.host.yaml');
  if (text !== null) fs.writeFileSync(file, text, 'utf8');
  return { cfg, file };
}

const SAMPLE = [
  'schema_version: 1',
  'node_id: pc-a',
  '',
  '# 手元のクローン（速度最適化）',
  'repos:',
  '  - url: https://example/app.git',
  '    local: /home/me/mirrors/app',
  '',
  'budget:',
  '  max_concurrent: 4',
  '',
].join('\n');

test('接続先: 宣言が無ければ空（既定＝実行 PC 自身の ollama）', () => {
  const { cfg } = hostConfig(SAMPLE);
  const info = ollama.loadOllamaHost(cfg);
  assert.strictEqual(info.host, '');
  assert.strictEqual(info.exists, true);
  assert.ok(info.file.endsWith('agent-project.host.yaml'));
});

test('接続先: 宣言された host を読む（timeout_sec も）', () => {
  const { cfg } = hostConfig(`${SAMPLE}ollama:\n  host: http://gpu-box:11434\n  timeout_sec: 900\n`);
  const info = ollama.loadOllamaHost(cfg);
  assert.strictEqual(info.host, 'http://gpu-box:11434');
  assert.strictEqual(info.timeoutSec, 900);
});

test('接続先: host.yaml が無い端末では保存せずエラーにする（空の宣言を作らない）', () => {
  const { cfg, file } = hostConfig(null);
  assert.strictEqual(ollama.loadOllamaHost(cfg).file, '');
  assert.throws(() => ollama.saveOllamaHost(cfg, { host: 'http://gpu-box:11434' }),
                /ノード宣言/);
  assert.strictEqual(fs.existsSync(file), false);
});

test('接続先: 保存は ollama ブロックを足し、他の宣言とコメントを保つ', () => {
  const { cfg, file } = hostConfig(SAMPLE);
  const saved = ollama.saveOllamaHost(cfg, { host: 'http://gpu-box:11434' });
  assert.strictEqual(saved.host, 'http://gpu-box:11434');
  const text = fs.readFileSync(file, 'utf8');
  assert.ok(text.includes('# 手元のクローン（速度最適化）'), 'コメントが残る');
  assert.ok(text.includes('  max_concurrent: 4'), '他ブロックが残る');
  assert.ok(/ollama:\n {2}host: "http:\/\/gpu-box:11434"/.test(text));
});

test('接続先: 2 度目の保存は host 行だけを差し替える（ブロックが増えない）', () => {
  const { cfg, file } = hostConfig(SAMPLE);
  ollama.saveOllamaHost(cfg, { host: 'http://gpu-box:11434' });
  ollama.saveOllamaHost(cfg, { host: 'http://other:11434' });
  const text = fs.readFileSync(file, 'utf8');
  assert.strictEqual((text.match(/^ollama:/gm) || []).length, 1);
  assert.strictEqual(ollama.loadOllamaHost(cfg).host, 'http://other:11434');
});

test('接続先: 空欄の保存は宣言を消す（既定の localhost へ戻す）', () => {
  const { cfg, file } = hostConfig(`${SAMPLE}ollama:\n  host: http://gpu-box:11434\n`);
  const saved = ollama.saveOllamaHost(cfg, { host: '' });
  assert.strictEqual(saved.host, '');
  const text = fs.readFileSync(file, 'utf8');
  assert.ok(!/^ollama:/m.test(text), '中身の無い ollama: は残さない');
});

test('接続先: 空欄でも他のキーが残っていればブロックは保つ', () => {
  const { cfg, file } = hostConfig(`${SAMPLE}ollama:\n  host: http://gpu-box:11434\n  timeout_sec: 900\n`);
  ollama.saveOllamaHost(cfg, { host: '' });
  const text = fs.readFileSync(file, 'utf8');
  assert.ok(/^ollama:/m.test(text));
  assert.ok(text.includes('timeout_sec: 900'));
});

test('接続先: 書き方が不正な値は保存前に弾く（宣言を壊さない）', () => {
  const { cfg, file } = hostConfig(SAMPLE);
  for (const bad of ['http://gpu box:11434', 'gpu"box', 'host # コメント', 'http://']) {
    assert.throws(() => ollama.saveOllamaHost(cfg, { host: bad }), /接続先/);
  }
  assert.strictEqual(fs.readFileSync(file, 'utf8'), SAMPLE);
  // scheme 省略（host:port）は受ける（アダプタ側が http:// を補う）
  assert.strictEqual(ollama.validateHost('gpu-box:11434'), 'gpu-box:11434');
});

test('接続先: 1 行表記の ollama: は黙って壊さず、直す場所を言って断る', () => {
  const { cfg, file } = hostConfig(`${SAMPLE}ollama: {host: http://gpu-box:11434}\n`);
  assert.throws(() => ollama.saveOllamaHost(cfg, { host: 'http://other:11434' }), /直接編集/);
  assert.ok(fs.readFileSync(file, 'utf8').includes('ollama: {host: http://gpu-box:11434}'));
});

test('接続先: JSON 形式のノード宣言でも読み書きできる', () => {
  const cfg = engineConfig([]);
  const file = path.join(cfg.engine.home, 'agent-project.host.json');
  fs.writeFileSync(file, JSON.stringify({ schema_version: 1, node_id: 'pc-a' }, null, 2), 'utf8');
  const saved = ollama.saveOllamaHost(cfg, { host: 'http://gpu-box:11434' });
  assert.strictEqual(saved.host, 'http://gpu-box:11434');
  assert.strictEqual(JSON.parse(fs.readFileSync(file, 'utf8')).node_id, 'pc-a');
  ollama.saveOllamaHost(cfg, { host: '' });
  assert.strictEqual(JSON.parse(fs.readFileSync(file, 'utf8')).ollama, undefined);
});

console.log(`\n${passed} ollama host tests passed`);
