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
  fs.mkdirSync(path.join(root, '.agents'), { recursive: true });
  const yaml = path.join(root, '.agents', 'agent-project.yaml');
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

test('splitCommand はクォート付きの空白入りパスを 1 要素に保つ', () => {
  assert.deepStrictEqual(actions.splitCommand('agent-project'), ['agent-project']);
  assert.deepStrictEqual(
    actions.splitCommand('python3 /opt/tools/agent-project.py'),
    ['python3', '/opt/tools/agent-project.py']
  );
  assert.deepStrictEqual(
    actions.splitCommand('"C:\\Program Files\\Python\\python.exe" agent-project.py'),
    ['C:\\Program Files\\Python\\python.exe', 'agent-project.py']
  );
  assert.deepStrictEqual(actions.splitCommand("  'a b'  c "), ['a b', 'c']);
});

async function asyncTest(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

(async () => {
  await asyncTest('runProjectCli は shell を介さず引数を安全に渡す', async () => {
    // 特殊文字（% $ ; スペース）入りの引数が変質せず届く（旧 shell:true 経路の欠陥の固定）
    const arg = 'reason with space %PATH% $HOME ;echo pwned';
    const res = await actions.runProjectCli(
      `${JSON.stringify(process.execPath)} -e console.log(process.argv[1])`,
      [arg],
      10000
    );
    assert.strictEqual(res.output, arg);
  });

  await asyncTest('runProjectCli は起動失敗を分かりやすいエラーで返す', async () => {
    await assert.rejects(
      () => actions.runProjectCli('no-such-cli-xyz', ['status'], 5000),
      /agent-project を起動できません/
    );
  });

  console.log(`\n${passed} tests passed (cli-config)`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
