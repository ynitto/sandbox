'use strict';

// `herd` — 実行レベルの構成で人が打つ 1 語が、実測由来の具体候補へ展開されるまで。
//
// 人が打つのは `herd` だけ（モデル欄は空）。どの (agent_cli, model) を使うかは
// 用途ごとに違い、それを知っているのは実測である。ここはその展開と、展開できない
// ときに推測しないことの網。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const REPO = path.join(__dirname, '..', '..', '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'herd-family-'));
const agentsDir = path.join(tmp, 'agents');
fs.mkdirSync(agentsDir, { recursive: true });

// 一族は `command[0] === 'agent-herd'` で決まる。クラウドはこの入口を通らない。
function writeAgent(name, spec) {
  fs.writeFileSync(path.join(agentsDir, `${name}.json`), JSON.stringify(spec, null, 2));
}
writeAgent('aider', { name: 'aider', command: ['agent-herd', 'aider'], default_model: 'gemma4:e4b' });
writeAgent('ollama', { name: 'ollama', command: ['agent-herd', 'ollama', '{model}'], default_model: 'gemma4:e4b' });
writeAgent('opencode', { name: 'opencode', command: ['agent-herd', 'opencode'] });
writeAgent('nova', { name: 'nova', command: ['nova-cli'], default_model: 'nova-1' });

process.env.KIRO_AGENTS_DIR = agentsDir;
const herdFamily = require('../src/features/orchestration/main/herd-family');

const cfg = {};
const qualifications = {
  version: 1,
  revision: 3,
  candidates: [
    { agent_cli: 'aider', model: 'gemma4:e4b', qualifications: {} },
    { agent_cli: 'ollama', model: 'gemma4:e4b', qualifications: {} },
    { agent_cli: 'ollama', model: 'gemma4:12b', qualifications: {} },
    { agent_cli: 'nova', model: 'nova-1', qualifications: {} },
  ],
};

test('一族は command[0] が agent-herd の定義から機械的に決まる', () => {
  assert.deepStrictEqual(herdFamily.members(cfg), ['aider', 'ollama', 'opencode']);
  assert.ok(!herdFamily.members(cfg).includes('nova'), 'クラウド CLI は入口を通らない');
});

test('herd 行は実測が知っている一族の (agent_cli, model) へ展開される', () => {
  const { tiers, expanded, unresolved } = herdFamily.expandTiers({
    small: { order: 1, candidates: [{ agent_cli: 'herd' }] },
  }, { memberNames: herdFamily.members(cfg), qualifications });
  assert.strictEqual(expanded, true);
  assert.deepStrictEqual(unresolved, []);
  assert.deepStrictEqual(tiers.small.candidates, [
    { agent_cli: 'aider', model: 'gemma4:e4b' },
    { agent_cli: 'ollama', model: 'gemma4:e4b' },
    { agent_cli: 'ollama', model: 'gemma4:12b' },
  ]);
});

test('一族の外の候補は展開に混ざらない', () => {
  const { tiers } = herdFamily.expandTiers({
    small: { order: 1, candidates: [{ agent_cli: 'herd' }] },
  }, { memberNames: herdFamily.members(cfg), qualifications });
  assert.ok(!tiers.small.candidates.some((c) => c.agent_cli === 'nova'));
});

test('モデル欄を書くとその 1 つに縛る（空＝おまかせ）', () => {
  const { tiers } = herdFamily.expandTiers({
    small: { order: 1, candidates: [{ agent_cli: 'herd', model: 'gemma4:12b' }] },
  }, { memberNames: herdFamily.members(cfg), qualifications });
  assert.deepStrictEqual(tiers.small.candidates, [
    { agent_cli: 'ollama', model: 'gemma4:12b' },
  ]);
});

test('具体名で書いた候補はそのまま残る（herd と混在できる）', () => {
  const { tiers } = herdFamily.expandTiers({
    small: { order: 1, candidates: [{ agent_cli: 'herd' }] },
    medium: { order: 2, candidates: [{ agent_cli: 'claude', model: 'sonnet' }] },
  }, { memberNames: herdFamily.members(cfg), qualifications });
  assert.deepStrictEqual(tiers.medium.candidates, [{ agent_cli: 'claude', model: 'sonnet' }]);
});

test('展開できないときは推測せず、その行を落として理由を返す', () => {
  const { tiers, unresolved } = herdFamily.expandTiers({
    small: { order: 1, candidates: [{ agent_cli: 'herd' }] },
  }, { memberNames: herdFamily.members(cfg), qualifications: { version: 1, revision: 0, candidates: [] } });
  assert.deepStrictEqual(tiers.small.candidates, []);
  assert.deepStrictEqual(unresolved, [{ tier: 'small', agent_cli: 'herd' }]);
});

test('展開は重複を畳む', () => {
  const { tiers } = herdFamily.expandTiers({
    small: {
      order: 1,
      candidates: [{ agent_cli: 'herd' }, { agent_cli: 'ollama', model: 'gemma4:e4b' }],
    },
  }, { memberNames: herdFamily.members(cfg), qualifications });
  const ids = tiers.small.candidates.map((c) => `${c.agent_cli}/${c.model}`);
  assert.deepStrictEqual([...new Set(ids)], ids, '同じ候補が 2 度並ばない');
});

test('許可リストは実在する定義と herd だけを通す（禁止リストにしない）', () => {
  const allowed = herdFamily.allowedAgentNames(cfg);
  assert.ok(allowed.has('herd'));
  assert.ok(allowed.has('ollama'));
  assert.ok(allowed.has('claude'), '組み込みも通る');
  assert.ok(!allowed.has('ollama-verify'), 'profile の綴りは定義ではないので通らない');
  assert.ok(!allowed.has('typo-cli'));
});

test('herd 判定は大小・空白を吸収する', () => {
  assert.strictEqual(herdFamily.isHerdCandidate({ agent_cli: ' HERD ' }), true);
  assert.strictEqual(herdFamily.isHerdCandidate({ agent_cli: 'ollama' }), false);
  assert.strictEqual(herdFamily.isHerdCandidate(null), false);
});

console.log(`\n${passed} tests passed (herd-family)`);
