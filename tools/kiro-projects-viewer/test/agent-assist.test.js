'use strict';

// agent.js（エージェント CLI 連携・charter 補完層）の軽量テスト。追加依存なしで
// `node test/agent-assist.test.js` で走る。CLI の実行（spawn）はしない —
// コマンド組み立て・設定解決・応答パースの純関数だけを検証する。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agent = require('../src/main/agent');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- buildCommand（kiro-project の _run_kiro_cli と同じ流儀） ---
test('buildCommand: kiro は kiro-cli chat にプロンプトを argv 渡し', () => {
  const c = agent.buildCommand('kiro', '', 'PROMPT');
  assert.strictEqual(c.command, 'kiro-cli');
  assert.deepStrictEqual(c.args.slice(0, 3), ['chat', '--no-interactive', '--trust-all-tools']);
  assert.strictEqual(c.args[c.args.length - 1], 'PROMPT');
  assert.strictEqual(c.stdin, null);
});

test('buildCommand: claude はヘッドレス + stdin 渡し、モデルは --model', () => {
  const c = agent.buildCommand('claude', 'sonnet', 'PROMPT');
  assert.strictEqual(c.command, 'claude');
  assert.ok(c.args.includes('-p') && c.args.includes('--output-format'));
  assert.ok(c.args.includes('--model') && c.args.includes('sonnet'));
  assert.strictEqual(c.stdin, 'PROMPT');
  assert.ok(!c.args.includes('PROMPT'), 'プロンプトは argv に載せない');
});

test('buildCommand: copilot は -p 渡し + 非対話の必須フラグ', () => {
  const c = agent.buildCommand('copilot', 'gpt-5', 'PROMPT');
  assert.strictEqual(c.command, 'copilot');
  assert.ok(c.args.includes('-s'), '-s（応答本文のみ）');
  assert.ok(c.args.includes('--allow-all-tools'), '非対話モードの必須フラグ');
  const i = c.args.indexOf('-p');
  assert.strictEqual(c.args[i + 1], 'PROMPT');
  assert.ok(c.args.includes('--model') && c.args.includes('gpt-5'));
  assert.strictEqual(c.stdin, null);
});

test('buildCommand: モデル未指定なら --model を付けない', () => {
  for (const cli of ['kiro', 'claude', 'copilot']) {
    assert.ok(!agent.buildCommand(cli, '', 'x').args.includes('--model'), cli);
  }
});

// --- resolveAgent（⚙ 設定 > プロジェクト設定 > 既定 kiro） ---
test('resolveAgent: ⚙ 設定の明示指定が最優先（プロジェクト設定より強い）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.writeFileSync(path.join(tmp, 'kiro-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: 'copilot', model: 'gpt-5' } }, tmp);
    assert.strictEqual(r.cli, 'copilot');
    assert.strictEqual(r.model, 'gpt-5');
    assert.strictEqual(r.source, 'settings');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: 設定が自動（空）ならプロジェクトの kiro-project.yaml に従う', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.writeFileSync(path.join(tmp, 'kiro-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: '', model: '' } }, tmp);
    assert.strictEqual(r.cli, 'claude');
    assert.strictEqual(r.model, 'opus');
    assert.strictEqual(r.source, 'project');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: .kiro/ 配下の設定ファイルも見る（本体の _find_config と同順）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.mkdirSync(path.join(tmp, '.kiro'));
    fs.writeFileSync(path.join(tmp, '.kiro', 'kiro-project.yaml'), 'agent_cli: copilot\n');
    const r = agent.resolveAgent({}, tmp);
    assert.strictEqual(r.cli, 'copilot');
    assert.strictEqual(r.source, 'project');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: ⚙ 設定で CLI を切り替えたときはプロジェクトの model を引き継がない', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    // プロジェクトは claude 用モデル。設定で copilot に切り替えたら model は CLI 既定（空）
    fs.writeFileSync(path.join(tmp, 'kiro-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: 'copilot' } }, tmp);
    assert.strictEqual(r.cli, 'copilot');
    assert.strictEqual(r.model, '');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: タイムアウトは秒指定・下限 30 秒', () => {
  assert.strictEqual(agent.resolveAgent({ agent: { cli: 'kiro', timeoutSec: 60 } }, null).timeoutMs, 60000);
  assert.strictEqual(agent.resolveAgent({ agent: { cli: 'kiro', timeoutSec: 5 } }, null).timeoutMs, 30000);
});

// --- 応答パース ---
test('extractJson: 説明文が混じっても最初の {…} を拾う', () => {
  const obj = agent.extractJson('了解です。\n{"goal": "G", "acceptance": ["a"]}\n以上です。');
  assert.deepStrictEqual(obj, { goal: 'G', acceptance: ['a'] });
  assert.strictEqual(agent.extractJson('JSON なし'), null);
  assert.strictEqual(agent.extractJson('{壊れた json}'), null);
});

test('stripFence: コードフェンスに包まれた charter を剥がす', () => {
  assert.strictEqual(agent.stripFence('```markdown\n# Charter: x\n## goal\n```'), '# Charter: x\n## goal');
  assert.strictEqual(agent.stripFence('# Charter: x'), '# Charter: x');
});

test('normalizeDraftFields: 配列/文字列/箇条書き前置きを改行区切りへ正規化', () => {
  const f = agent.normalizeDraftFields({
    goal: '  G  ',
    constraints: ['- a', 'b '],
    deliverables: 'x\n- y\n\n',
    acceptance: null,
  });
  assert.strictEqual(f.goal, 'G');
  assert.strictEqual(f.constraints, 'a\nb');
  assert.strictEqual(f.deliverables, 'x\ny');
  assert.strictEqual(f.acceptance, '');
  assert.strictEqual(f.assumptions, '');
});

// --- プロンプト（charter.md の書式契約が載っていること） ---
test('charterDraftPrompt: JSON キー・acceptance 規約・入力欄が載る', () => {
  const p = agent.charterDraftPrompt({ name: 'demo', goal: 'G', memo: '背景メモ' });
  assert.ok(p.includes('"acceptance"') && p.includes('"assumptions"'));
  assert.ok(p.includes('終了コード 0'), 'acceptance のコマンド化規約');
  assert.ok(p.includes('demo') && p.includes('背景メモ'));
});

test('charterRefinePrompt: セクション書式の維持と repos 不変を指示する', () => {
  const p = agent.charterRefinePrompt('# Charter: x\n## goal\nG');
  assert.ok(p.includes('# Charter:') && p.includes('## goal'));
  assert.ok(p.includes('repos の URL・owns 等は変更しない'));
  assert.ok(p.includes('# Charter: x'), '元の charter 全文を渡す');
});

console.log(`\n${passed} tests passed (agent-assist)`);
