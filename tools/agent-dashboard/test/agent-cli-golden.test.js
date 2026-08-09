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
    // --tools は write のときだけ生える（readonly は素の text→text のまま）。
    // 予算（--max-rounds / --command-timeout）は「品質を時間で買う」側へ倒した既定。
    write: ['agent-ollama', '--think', 'on', 'M', '--tools', 'bash',
            '--max-rounds', '30', '--command-timeout', '900'],
    readonly: ['agent-ollama', '--think', 'on', 'M'],
    interactive: ['agent-ollama', '--tui', '--think', 'on', 'M'],
  },
  'ollama-json': {
    // JSON 契約の役割用（--format json で文法から強制する。道具は持たせない）。
    write: ['agent-ollama', '--think', 'on', '--format', 'json', 'M'],
    readonly: ['agent-ollama', '--think', 'on', '--format', 'json', 'M'],
  },
  'ollama-read': {
    // 探索が要る readonly 役割用（write 経路に read セットを載せ、権限はゲートが絞る）。
    write: ['agent-ollama', '--think', 'on', 'M', '--tools', 'read',
            '--max-rounds', '30', '--command-timeout', '900'],
    readonly: ['agent-ollama', '--think', 'on', 'M'],
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
    if (want.interactive) {
      assert.deepStrictEqual(agentCli.interactiveCmd(spec, 'M'), want.interactive, name);
    }
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

// 「安いほう」の判断を 1 つの数へ集める以上、その数が**同梱定義すべてで宣言され**、
// ローカル実行がクラウド実行より厳密に安い、が守られていないと降格・昇格が意味を失う。
// 個別の値をスナップショットしても不変条件は守れないので、全定義を読んで関係を確かめる。
const LOCAL_ENGINES = new Set(['ollama', 'ollama-json', 'ollama-read', 'opencode']);

test('相対コストは全同梱定義で宣言され、ローカル < クラウドの関係を保つ', () => {
  const dir = agentCli.bundledDir();
  const names = fs.readdirSync(dir).filter((f) => f.endsWith('.json')).map((f) => path.parse(f).name);
  const costs = new Map(names.map((n) => [n, agentCli.loadCli(n, null, { useCache: false }).relativeCost]));

  for (const [name, cost] of costs) {
    assert.ok(Number.isFinite(cost) && cost >= 0, `${name}: relative_cost が数値でない (${cost})`);
    const raw = JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), 'utf8'));
    assert.ok('relative_cost' in raw, `${name}: relative_cost を宣言していない（既定 1 に落ちる）`);
  }

  const local = [...costs].filter(([n]) => LOCAL_ENGINES.has(n));
  const cloud = [...costs].filter(([n]) => !LOCAL_ENGINES.has(n));
  assert.ok(local.length && cloud.length, '両方の分類に定義がある');
  const maxLocal = Math.max(...local.map(([, c]) => c));
  const minCloud = Math.min(...cloud.map(([, c]) => c));
  assert.ok(maxLocal < minCloud,
    `ローカルがクラウド以上に高い: local=${JSON.stringify(local)} cloud=${JSON.stringify(cloud)}`);
});

console.log(`\n${passed} tests passed (agent-cli-golden)`);
