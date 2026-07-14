'use strict';

// findProjectConfig / startProject の --config 配線を固定する。
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const actions = require('../src/main/actions');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('findProjectConfig は状態ルート直下の yaml も見つける', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-cfg-'));
  const state = path.join(tmp, 'proj-agent-state', '.agent-project');
  const source = path.join(tmp, 'proj', '.agent-project');
  fs.mkdirSync(state, { recursive: true });
  fs.mkdirSync(source, { recursive: true });
  const yaml = path.join(state, 'agent-project.yaml');
  fs.writeFileSync(yaml, 'root: .\n');
  // fromStateWorktree 相当: source だけ渡すと yaml が無い
  assert.strictEqual(actions.findProjectConfig(source), null);
  // 状態ルートも渡せば拾える
  assert.strictEqual(actions.findProjectConfig(source, state), yaml);
});

test('findProjectConfig は本体側 .agent/ も従来どおり探す', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-cfg2-'));
  const root = path.join(tmp, 'ws');
  fs.mkdirSync(path.join(root, '.agent'), { recursive: true });
  const yaml = path.join(root, '.agent', 'agent-project.yaml');
  fs.writeFileSync(yaml, 'root: .\n');
  assert.strictEqual(actions.findProjectConfig(root), yaml);
});

test('startProject ソースは findProjectConfig と cwd を使う', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '../src/features/agent-project/main/actions.js'),
    'utf8'
  );
  const block = src.match(/async function startProject[\s\S]*?\n\}/);
  assert.ok(block, 'startProject がある');
  assert.ok(/findProjectConfig\(root,\s*dir\)/.test(block[0]), '--config 探索に状態 dir も渡す');
  assert.ok(/--config/.test(block[0]), '見つかれば --config を付ける');
  assert.ok(/runProjectCli\([^)]*cwd/.test(block[0]) || /120000,\s*cwd/.test(block[0]),
    'cwd を設定ディレクトリに合わせる');
});

console.log(`\n${passed} tests passed (cli-config)`);
