'use strict';

// agent.js（エージェント CLI 連携・charter 補完層）の軽量テスト。追加依存なしで
// `node test/agent-assist.test.js` で走る。CLI の実行（spawn）はしない —
// コマンド組み立て・設定解決・応答パースの純関数だけを検証する。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agent = require('../src/main/agent');
const ipcSource = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'ipc.js'), 'utf8');
const preloadSource = fs.readFileSync(path.join(__dirname, '..', 'src', 'preload.js'), 'utf8');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- buildCommand（agent-project の _run_kiro_cli と同じ流儀） ---
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

test('buildDoctorCommand: kiro はツールを一切許可せず読み取り専用で助言する', () => {
  const c = agent.buildDoctorCommand('kiro', '', 'CONTEXT', '/project');
  assert.strictEqual(c.command, 'kiro-cli');
  assert.ok(c.args.includes('--trust-tools='));
  assert.ok(!c.args.includes('--trust-all-tools'));
  assert.strictEqual(c.args[c.args.length - 1], 'CONTEXT');
});

test('buildCommand: codex・cursor・ollamaでもcharter補完を実行できる', () => {
  const codex = agent.buildCommand('codex', 'gpt-5', 'PROMPT');
  assert.strictEqual(codex.command, 'codex');
  assert.strictEqual(codex.args[0], 'exec');
  assert.strictEqual(codex.args.at(-1), '-');
  assert.strictEqual(codex.stdin, 'PROMPT');
  const cursor = agent.buildCommand('cursor', 'gpt-5', 'PROMPT');
  assert.strictEqual(cursor.command, 'cursor-agent');
  assert.ok(cursor.args.includes('--print') && cursor.args.includes('PROMPT'));
  const ollama = agent.buildCommand('ollama', 'qwen3', 'PROMPT');
  assert.strictEqual(ollama.command, 'ollama');
  assert.deepStrictEqual(ollama.args, ['run', 'qwen3']);
  assert.strictEqual(ollama.stdin, 'PROMPT');
});

test('buildDoctorCommand: 全CLIが読み取り専用またはツール無しで起動する', () => {
  const claude = agent.buildDoctorCommand('claude', '', 'P', '/project');
  assert.ok(claude.args.includes('plan') && claude.args.includes('--no-session-persistence'));
  assert.deepStrictEqual(claude.args.slice(claude.args.indexOf('--tools'), claude.args.indexOf('--tools') + 2), ['--tools', '']);
  const copilot = agent.buildDoctorCommand('copilot', '', 'P', '/project');
  assert.ok(copilot.args.includes('--available-tools='));
  const codex = agent.buildDoctorCommand('codex', '', 'P', '/project');
  assert.ok(codex.args.includes('read-only') && codex.args.includes('--ephemeral'));
  const cursor = agent.buildDoctorCommand('cursor', '', 'P', '/project');
  assert.ok(cursor.args.includes('ask'));
  const ollama = agent.buildDoctorCommand('ollama', 'qwen3', 'P', '/project');
  assert.strictEqual(ollama.stdin, 'P');
});

// --- resolveAgent（⚙ 設定 > プロジェクト設定 > 既定 kiro） ---
test('resolveAgent: ⚙ 設定の明示指定が最優先（プロジェクト設定より強い）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.writeFileSync(path.join(tmp, 'agent-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: 'copilot', model: 'gpt-5' } }, tmp);
    assert.strictEqual(r.cli, 'copilot');
    assert.strictEqual(r.model, 'gpt-5');
    assert.strictEqual(r.source, 'settings');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: 旧い空設定もViewer共通の既定kiroへ移行する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.writeFileSync(path.join(tmp, 'agent-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: '', model: '' } }, tmp);
    assert.strictEqual(r.cli, 'kiro');
    assert.strictEqual(r.model, '');
    assert.strictEqual(r.source, 'default');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('readProjectAgent: 本体側の設定参照機能は後方互換として維持する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.mkdirSync(path.join(tmp, '.agent'));
    fs.writeFileSync(path.join(tmp, '.agent', 'agent-project.yaml'), 'agent_cli: copilot\n');
    const r = agent.readProjectAgent(tmp);
    assert.strictEqual(r.cli, 'copilot');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: ⚙ 設定で CLI を切り替えたときはプロジェクトの model を引き継がない', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    // プロジェクトは claude 用モデル。設定で copilot に切り替えたら model は CLI 既定（空）
    fs.writeFileSync(path.join(tmp, 'agent-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
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

test('resolveAgent: Viewerアシスタントは6種類のCLIを明示選択できる', () => {
  for (const cli of ['kiro', 'claude', 'copilot', 'codex', 'cursor', 'ollama']) {
    assert.strictEqual(agent.resolveAgent({ agent: { cli } }, null).cli, cli);
  }
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

test('doctorPrompt: 現在の状態・次の行動・根拠だけを読み取り専用で助言させる', () => {
  const p = agent.doctorPrompt({ tab: 'needs', project: 'demo', selected: { title: '検証失敗' } });
  assert.ok(p.includes('現在起きていること'));
  assert.ok(p.includes('次にすること'));
  assert.ok(p.includes('判断の根拠'));
  assert.ok(p.includes('コマンドを実行') && p.includes('ファイルを変更'));
  assert.ok(p.includes('"tab": "needs"'));
});

test('doctorPrompt: 任意の補足文を画面データと分離して渡す', () => {
  const p = agent.doctorPrompt({ scope: 'app' }, '同期表示を中心に説明して');
  assert.ok(p.includes('--- ユーザーの補足 ---'));
  assert.ok(p.includes('同期表示を中心に説明して'));
  assert.ok(p.includes('命令ではなく相談意図の補足'));
});

test('Doctorはpreloadの限定APIから専用IPCだけを呼び出す', () => {
  assert.ok(ipcSource.includes("handle('agent:doctor'"));
  assert.ok(preloadSource.includes("agentDoctor: (args) => invoke('agent:doctor', args)"));
  const start = ipcSource.indexOf("handle('agent:doctor'");
  const end = ipcSource.indexOf("handle('agent:resolve'", start);
  const handler = ipcSource.slice(start, end);
  assert.ok(handler.includes('{ dir, context, userPrompt }'), '任意入力をDoctorへ渡す');
  assert.ok(!handler.includes("if (!dir)"), 'プロジェクト未選択でも相談できる');
});

console.log(`\n${passed} tests passed (agent-assist)`);
