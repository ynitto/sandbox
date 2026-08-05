'use strict';

// 同梱の CLI 定義（agents/<name>.json）から出る argv を固定する（S9）。
//
// ローダは Python（agentcore/agentcli.py）と JS（agentCli.js）の 2 実装ある。UI の応答性の
// ために dashboard だけ自前実装を持つ、という判断の代償がここ——**同じ定義から違う argv が
// 出る**とツールによって CLI の挙動が変わり、しかも気づけない。期待値は Python 側のテスト
// （tools/agent-tools/agentcore/agentcore/tests/test_agentcli.py の GOLDEN）と同じものを置き、
// 片方を直したらもう片方も落ちるようにしてある。
//
// 値そのものは「S9 移行前にコードへハードコードされていた argv」でもある（回帰防止）。

const assert = require('assert');
const fs = require('fs');
const path = require('path');
process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');
const agentCli = require('../src/features/agent-project/main/agentCli');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const GOLDEN = {
  kiro: {
    write: ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'M', 'P'],
    readonly: ['kiro-cli', 'chat', '--no-interactive', '--trust-tools=', '--model', 'M', 'P'],
    interactive: ['kiro-cli', 'chat', '--trust-all-tools', '--model', 'M'],
  },
  claude: {
    write: ['claude', '-p', '--output-format', 'text',
            '--dangerously-skip-permissions', '--model', 'M'],
    readonly: ['claude', '-p', '--output-format', 'text',
               '--permission-mode', 'plan', '--tools', '', '--model', 'M'],
    interactive: ['claude', '--model', 'M'],
  },
  copilot: {
    write: ['copilot', '-s', '--allow-all-tools', '--no-color',
            '--allow-all-paths', '--model', 'M', '-p', 'P'],
    readonly: ['copilot', '-s', '--allow-all-tools', '--no-color',
               '--available-tools=', '--disable-builtin-mcps',
               '--no-custom-instructions', '--model', 'M', '-p', 'P'],
    interactive: ['copilot', '--model', 'M'],
  },
  cursor: {
    write: ['cursor-agent', '-p', '--output-format', 'text', '--force', '--model', 'M'],
    readonly: ['cursor-agent', '-p', '--output-format', 'text', '--force',
               '--mode', 'ask', '--model', 'M'],
    interactive: ['cursor-agent', '--model', 'M'],
  },
  ollama: {
    write: ['agent-ollama', 'M'],
    readonly: ['agent-ollama', 'M'],
    interactive: ['ollama', 'run', 'M'],
  },
  opencode: {
    write: ['agent-opencode', '--auto', '--model', 'M'],
    readonly: ['agent-opencode', '--agent', 'plan', '--model', 'M'],
    interactive: ['opencode', '--model', 'M'],
  },
};

test('同梱定義から出る argv が Python ローダと一致する', () => {
  for (const [name, want] of Object.entries(GOLDEN)) {
    const spec = agentCli.loadCli(name);
    assert.deepStrictEqual(agentCli.headlessCmd(spec, 'M', 'P').argv, want.write, name);
    assert.deepStrictEqual(agentCli.headlessCmd(spec, 'M', 'P', { readonly: true }).argv,
                           want.readonly, name);
    assert.deepStrictEqual(agentCli.interactiveCmd(spec, 'M'), want.interactive, name);
  }
});

test('codex は {output_file} を伏せれば一致する（実行毎にパスが変わる）', () => {
  const spec = agentCli.loadCli('codex');
  const r = agentCli.headlessCmd(spec, 'M', 'P');
  const argv = r.argv.map((t) => (t === r.outputFile ? '<out>' : t));
  assert.deepStrictEqual(argv, ['codex', 'exec', '--skip-git-repo-check', '--color', 'never',
                                '--output-last-message', '<out>',
                                '--dangerously-bypass-approvals-and-sandbox', '--model', 'M', '-']);
  assert.strictEqual(r.stdin, 'P');
});

test('同梱定義がすべて読める（壊れた定義を同梱しない）', () => {
  const dir = agentCli.bundledDir();
  assert.ok(dir, '同梱 agents/ が見つかる');
  const names = fs.readdirSync(dir).filter((f) => f.endsWith('.json')).map((f) => path.parse(f).name);
  assert.ok(names.length >= 6, `同梱定義が少なすぎます: ${names.join(', ')}`);
  for (const n of names) agentCli.loadCli(n, null, { useCache: false });
});

console.log(`\n${passed} tests passed (agent-cli-golden)`);
