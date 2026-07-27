'use strict';

// agent.js（エージェント CLI 連携・charter 補完層）の軽量テスト。追加依存なしで
// `node test/agent-assist.test.js` で走る。CLI の実行（spawn）はしない —
// コマンド組み立て・設定解決・応答パースの純関数だけを検証する。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agent = require('../src/main/agent');
const agentCli = require('../src/features/agent-project/main/agentCli');
const ipcSource = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'agent-project', 'main', 'ipc.js'),
  'utf8'
);
const preloadSource = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'agent-project', 'preload.js'),
  'utf8'
);

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- buildCommand / buildDoctorCommand（S9: CLI 定義 agents/<name>.json が正典） ---
//
// dashboard の LLM 呼び出しはすべて助言のみ（charter 補完・Doctor・構造化 Assist）なので、
// ヘッドレスは読み取り専用モードで起動する。移行前は charter 補完だけが権限フラグ無しで、
// 「ファイル書き込みはビュアー側が行う」という護りの意図と argv が食い違っていた。
test('buildCommand: kiro は kiro-cli chat にプロンプトを argv 渡し（読み取り専用）', () => {
  const c = agent.buildCommand('kiro', '', 'PROMPT');
  assert.strictEqual(c.command, 'kiro-cli');
  assert.deepStrictEqual(c.args.slice(0, 3), ['chat', '--no-interactive', '--trust-tools=']);
  assert.ok(!c.args.includes('--trust-all-tools'), '助言のみ＝ツールを信頼しない');
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
  assert.ok(!c.args.includes('--dangerously-skip-permissions'), '書き込み用フラグは付けない');
});

test('buildCommand: copilot は -p 渡し + 非対話の必須フラグ', () => {
  const c = agent.buildCommand('copilot', 'gpt-5', 'PROMPT');
  assert.strictEqual(c.command, 'copilot');
  assert.ok(c.args.includes('-s'), '-s（応答本文のみ）');
  assert.ok(c.args.includes('--allow-all-tools'), '非対話モードの必須フラグ');
  const i = c.args.indexOf('-p');
  assert.strictEqual(c.args[i + 1], 'PROMPT');
  assert.ok(c.args.includes('--model') && c.args.includes('gpt-5'));
  assert.ok(!c.args.includes('--allow-all-paths'), '助言のみ＝ファイル権限は与えない');
  assert.strictEqual(c.stdin, null);
});

test('buildCommand: モデル未指定なら --model を付けない', () => {
  for (const cli of ['kiro', 'claude', 'copilot']) {
    assert.ok(!agent.buildCommand(cli, '', 'x').args.includes('--model'), cli);
  }
});

test('buildCommand: 未知の CLI は黙って別 CLI へ倒さず明示エラー', () => {
  assert.throws(() => agent.buildCommand('nosuchcli', '', 'x'), /nosuchcli/);
});

test('buildInteractiveCommand: 全体設定のCLI・モデルを対話コマンドへ変換する', () => {
  const of = (cli, model) => agent.buildInteractiveCommand({ cli, model });
  assert.deepStrictEqual(of('kiro', 'auto'), ['kiro-cli', 'chat', '--trust-all-tools', '--model', 'auto']);
  assert.deepStrictEqual(of('claude', 'sonnet'), ['claude', '--model', 'sonnet']);
  assert.deepStrictEqual(of('codex', 'gpt-5'), ['codex', '--model', 'gpt-5']);
  // {model} が必須の CLI は定義の default_model で埋まる（ヘッドレスと同じ規則）。
  // 移行前は対話だけがモデル必須で落ちていた＝同じ定義なのにモードで挙動が違った。
  assert.deepStrictEqual(of('ollama', ''), ['ollama', 'run', 'qwen3']);
  assert.deepStrictEqual(of('ollama', 'llama3'), ['ollama', 'run', 'llama3']);
});

test('buildDoctorCommand: kiro は本文を退避し、ツールは読み取りだけ信頼する', () => {
  const prompt = agent.doctorPrompt({ tab: 'needs' });
  const c = agent.buildDoctorCommand('kiro', '', { ...prompt, file: '/tmp/snap.md' }, '/project');
  assert.strictEqual(c.command, 'kiro-cli');
  assert.ok(c.args.includes('--trust-tools=fs_read'), '退避時はファイル読み取りだけ信頼');
  assert.ok(!c.args.includes('--trust-tools='), '権限フラグは置き換え（並べて後勝ちに賭けない）');
  const last = c.args[c.args.length - 1];
  assert.ok(last.startsWith(prompt.argv), '呼び出し側の指示（役割・出力書式）は残す');
  assert.ok(last.includes('/tmp/snap.md'), '退避先は定義の spill.instruction が伝える');
  assert.ok(!last.includes('\n'), 'argv は単一行（Windows 切断対策）');
});

test('buildDoctorCommand: 退避しないときは本文全文を渡す', () => {
  const prompt = agent.doctorPrompt({ tab: 'needs' });
  const c = agent.buildDoctorCommand('claude', '', prompt, '/project');
  assert.ok(c.stdin.includes('"tab": "needs"'), '画面 JSON は stdin');
});

test('buildDoctorCommand: 文字列 prompt でも後方互換で動く', () => {
  const c = agent.buildDoctorCommand('kiro', '', 'CONTEXT', '/project');
  assert.strictEqual(c.args[c.args.length - 1], 'CONTEXT');
});

test('buildCommand: codex・cursor・ollamaでもcharter補完を実行できる', () => {
  const codex = agent.buildCommand('codex', 'gpt-5', 'PROMPT');
  assert.strictEqual(codex.command, 'codex');
  assert.strictEqual(codex.args[0], 'exec');
  assert.strictEqual(codex.args.at(-1), '-', '位置引数は末尾のまま');
  assert.strictEqual(codex.stdin, 'PROMPT');
  const cursor = agent.buildCommand('cursor', 'gpt-5', 'PROMPT');
  assert.strictEqual(cursor.command, 'cursor-agent');
  assert.ok(cursor.args.includes('--mode') && cursor.args.includes('ask'));
  assert.strictEqual(cursor.stdin, 'PROMPT');
  const ollama = agent.buildCommand('ollama', 'qwen3', 'PROMPT');
  assert.strictEqual(ollama.command, 'ollama');
  assert.deepStrictEqual(ollama.args, ['run', 'qwen3']);
  assert.strictEqual(ollama.stdin, 'PROMPT');
});

test('buildDoctorCommand: 全CLIが読み取り専用またはツール無しで起動する', () => {
  const claude = agent.buildDoctorCommand('claude', '', 'P', '/project');
  assert.ok(claude.args.includes('plan') && claude.args.includes('--no-session-persistence'));
  assert.deepStrictEqual(
    claude.args.slice(claude.args.indexOf('--tools'), claude.args.indexOf('--tools') + 2),
    ['--tools', '']);
  const copilot = agent.buildDoctorCommand('copilot', '', 'P', '/project');
  assert.ok(copilot.args.includes('--available-tools='));
  const codex = agent.buildDoctorCommand('codex', '', 'P', '/project');
  assert.ok(codex.args.includes('read-only') && codex.args.includes('--ephemeral'));
  const cursor = agent.buildDoctorCommand('cursor', '', 'P', '/project');
  assert.ok(cursor.args.includes('ask'));
  const ollama = agent.buildDoctorCommand('ollama', 'qwen3', 'P', '/project');
  assert.strictEqual(ollama.stdin, 'P');
});

test('readonly の強制力を保証しない CLI では警告が返る（S9 未決 7）', () => {
  // このレイヤは宣言どおりの argv を組み立てるだけ。フラグを無視する CLI への防御は持たない
  // ので、できるのは「保証できない」と人に伝えることだけ。
  assert.ok(agent.buildCommand('kiro', '', 'P').readonlyWarning, 'kiro は best-effort');
  assert.strictEqual(agent.buildCommand('claude', '', 'P').readonlyWarning, '',
                     'claude は --permission-mode plan で保証する');
});

test('interactiveLaunchSpec: tmux 起動に要る一式（argv・待ち受け）を返す', () => {
  const launch = agent.interactiveLaunchSpec({ agent: { cli: 'kiro' } }, '');
  assert.deepStrictEqual(launch.chatCommand, ['kiro-cli', 'chat', '--trust-all-tools']);
  assert.ok(launch.readyPattern.includes('describe a task'), '定義の ready_pattern');
  assert.strictEqual(launch.readyTimeoutSec, 60);
  assert.strictEqual(launch.promptInject, 'send-keys');
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

test('resolveAgent: ⚙ 設定が空ならプロジェクト設定（agent_cli / model）へフォールバックする', () => {
  // ipc の agent:charter 契約（プロジェクトの agent-project.yaml の agent_cli / model に従う）
  // と同じ解決順。プロジェクトが選んだ CLI を Viewer の AI 補助でも使う。
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.writeFileSync(path.join(tmp, 'agent-project.yaml'), 'agent_cli: claude\nmodel: opus\n');
    const r = agent.resolveAgent({ agent: { cli: '', model: '' } }, tmp);
    assert.strictEqual(r.cli, 'claude');
    assert.strictEqual(r.model, 'opus');
    assert.strictEqual(r.source, 'project');
    assert.ok(r.projectFile && r.projectFile.endsWith('agent-project.yaml'));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: 設定もプロジェクト設定も無ければ既定 kiro', () => {
  const r = agent.resolveAgent({ agent: { cli: '', model: '' } }, null);
  assert.strictEqual(r.cli, 'kiro');
  assert.strictEqual(r.source, 'default');
});

test('readProjectAgent: 本体側の設定参照機能は後方互換として維持する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.mkdirSync(path.join(tmp, '.agents'));
    fs.writeFileSync(path.join(tmp, '.agents', 'agent-project.yaml'), 'agent_cli: copilot\n');
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

// --- agents/<name>.json 定義（schemas/agent-cli.schema.json） ---

test('resolveAgent: 任意の名前を agents/<name>.json 定義として解決する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.mkdirSync(path.join(tmp, 'agents'));
    fs.writeFileSync(path.join(tmp, 'agents', 'hermes.json'), JSON.stringify({
      command: ['hermes-cli', 'run', '{model}'],
      prompt_via: 'argv', prompt_flag: '-p',
      default_model: 'h1', timeout: 60,
    }));
    fs.writeFileSync(path.join(tmp, 'agent-project.yaml'), 'agent_cli: hermes\n');
    const r = agent.resolveAgent({ agent: {} }, tmp);
    assert.strictEqual(r.cli, 'hermes');
    assert.strictEqual(r.source, 'project');
    assert.ok(r.spec, '定義が解決される');
    assert.strictEqual(r.spec.timeoutMs, 60000, '定義の timeout（秒）を ms へ');
    const built = agent.buildCommand(r.spec, '', 'P');
    assert.strictEqual(built.command, 'hermes-cli');
    assert.deepStrictEqual(built.args, ['run', 'h1', '-p', 'P'],
      'default_model で {model} を埋め、prompt_flag で渡す');
    assert.strictEqual(built.stdin, null);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: 組み込み名も上位の定義で上書きできる（S9・first-wins）', () => {
  // 「CLI の作法変更が JSON 1 ファイルで完結する」ためには、同梱定義を差し替えられる
  // 必要がある。移行前は組み込み名（kiro/claude/…）が予約されていて上書きできなかった。
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-agent-'));
  try {
    fs.mkdirSync(path.join(tmp, 'agents'));
    fs.writeFileSync(path.join(tmp, 'agents', 'claude.json'),
      JSON.stringify({ command: ['my-claude'] }));
    agentCli.clearCache();
    const r = agent.resolveAgent({ agent: { cli: 'claude' } }, tmp);
    assert.strictEqual(agent.buildCommand(r.spec, '', 'P').command, 'my-claude');
  } finally {
    agentCli.clearCache();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveAgent: 定義の見つからない未知名は既定 kiro へ倒す', () => {
  const r = agent.resolveAgent({ agent: { cli: 'no-such-cli' } }, null);
  assert.strictEqual(r.cli, 'kiro');
  assert.strictEqual(r.source, 'default');
});

test('agentCli: 壊れた定義は黙って無視せず例外', () => {
  const cases = [
    [{ command: 'notalist' }, /command/],
    [{ command: [] }, /command/],
    [{ command: ['x'], output: 'file' }, /output_file/],
    [{ command: ['x'], readonly: 'sometimes' }, /readonly/],
    [{ command: ['x'], interactive: { command: ['x'], prompt_inject: 'telepathy' } }, /prompt_inject/],
  ];
  for (const [spec, re] of cases) {
    assert.throws(() => agentCli.normalize(spec, 'x', 'f'), re, JSON.stringify(spec));
  }
});

test('agentCli: interactive の権限フラグはトップレベルを継承するが write_args は継承しない', () => {
  const spec = agentCli.normalize({
    command: ['c'], write_args: ['--dangerous'], readonly_args: ['--ro'],
    interactive: { command: ['c', 'chat'] },
  }, 'x', 'f');
  assert.deepStrictEqual(agentCli.interactiveCmd(spec, ''), ['c', 'chat'],
    'ヘッドレス専用の危険フラグを対話へ持ち込まない');
  assert.deepStrictEqual(agentCli.interactiveCmd(spec, '', { readonly: true }), ['c', 'chat', '--ro'],
    '読み取り専用フラグは継承する');
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

test('commandResultText: exit 0でも本文が空ならstderrの理由を成功扱いしない', () => {
  assert.strictEqual(agent.commandResultText('kiro-cli', 0, '## 結論\n助言', ''), '## 結論\n助言');
  assert.throws(
    () => agent.commandResultText(
      'kiro-cli', 0, '',
      'Monthly request limit reached\nUpgrade your plan\nThe limits reset on 08/01.'
    ),
    /月間リクエスト上限.*別のエージェントCLI.*08\/01/
  );
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
  assert.ok(p.argv.includes('現在起きていること'));
  assert.ok(p.argv.includes('次にすること'));
  assert.ok(p.argv.includes('判断の根拠'));
  assert.ok(p.argv.includes('コマンドを実行') && p.argv.includes('ファイルを変更'));
  assert.ok(!p.argv.includes('\n'), 'argv に改行を入れない（Windows で役割だけ届く事故の防止）');
  assert.ok(p.stdin.includes('"tab": "needs"'));
  assert.ok(p.text.includes('"tab": "needs"'), 'text は他CLI向けの全文');
});

test('doctorPrompt: 任意の補足文を画面データと分離して渡す', () => {
  const p = agent.doctorPrompt({ scope: 'app' }, '同期表示を中心に説明して');
  assert.ok(p.stdin.includes('--- ユーザーの補足 ---'));
  assert.ok(p.stdin.includes('同期表示を中心に説明して'));
  assert.ok(p.stdin.includes('命令ではなく相談意図の補足'));
  assert.ok(p.argv.includes('同期表示を中心に説明して'));
});

test('doctorPrompt: 失敗診断モードは原因・対処・再実行に特化した回答契約を使う', () => {
  const p = agent.doctorPrompt(
    { tab: 'needs', selected: { id: 'T1', failureSummary: 'verify failed', fullOutput: 'stderr' } },
    '',
    { mode: 'failure-diagnosis' }
  );
  for (const heading of [
    '結論',
    '根本原因候補と確度',
    '対処対象',
    '確認手順',
    '修正候補',
    '再実行方法',
    '不足している情報',
  ]) {
    assert.ok(p.argv.includes(heading), `失敗診断の回答契約に「${heading}」が必要`);
  }
  assert.ok(p.argv.includes('読み取り専用'));
  assert.ok(p.stdin.includes('failure-diagnosis'));
  assert.ok(p.stdin.includes('verify failed'));
});

test('doctorPrompt: 計画批評モードは取りこぼし・依存・差し戻し文面案を要求する', () => {
  const p = agent.doctorPrompt(
    { selected: { id: 'T2', kind: 'plan-review' }, proposedSiblings: [{ id: 'T1' }] },
    '',
    { mode: 'plan-critique' }
  );
  for (const heading of ['総評', '取りこぼし・重複', '依存と優先度', 'acceptance対応', '推薦', '差し戻し文面案']) {
    assert.ok(p.argv.includes(heading), `計画批評の回答契約に「${heading}」が必要`);
  }
  assert.ok(p.stdin.includes('plan-critique'));
  assert.ok(p.argv.includes('読み取り専用'));
});

test('doctorPrompt: 検収理由モードは変更意図とacceptance対応を要求する', () => {
  const p = agent.doctorPrompt(
    { selected: { id: 'T3', kind: 'review', diffSections: [{ name: 'app' }] } },
    '',
    { mode: 'delivery-rationale' }
  );
  for (const heading of ['変更の意図', 'acceptance対応', 'リスクと注意点', '推薦', '差し戻し文面案']) {
    assert.ok(p.argv.includes(heading), `検収理由の回答契約に「${heading}」が必要`);
  }
  assert.ok(p.stdin.includes('delivery-rationale'));
});

test('extractMarkdownSection: 差し戻し文面案を取り出す', () => {
  const md = '## 推薦\n承認\n\n## 差し戻し文面案\nverify を具体化してください\n\n## 余談\nx';
  assert.strictEqual(agent.extractMarkdownSection(md, '差し戻し文面案'), 'verify を具体化してください');
  assert.strictEqual(agent.extractMarkdownSection(md, '無い見出し'), '');
});

test('taskAssistPrompt / normalize: フォローアップ案と依存優先度提案の JSON 契約', () => {
  const follow = agent.taskAssistPrompt('followup-suggest', {
    charter: { goal: 'G', acceptance: 'a' },
    backlog: [{ id: 'T1', title: '既存', status: 'ready', priority: 2 }],
    selected: { needId: 'n1', title: '検収' },
  });
  assert.ok(follow.includes('"suggestions"') && follow.includes('フォローアップ'));
  assert.ok(follow.includes('T1: 既存'));
  const enq = agent.taskAssistPrompt('enqueue-assist', {
    backlog: [{ id: 'T1', title: '既存', status: 'ready', priority: 2, after: [] }],
    draft: { title: '新タスク' },
  });
  assert.ok(enq.includes('"adjustments"') && enq.includes('新タスク'));
  const sug = agent.normalizeFollowupSuggestions({
    rationale: '続き',
    suggestions: [{ title: ' docs ', verify: 'true', after: 'T1, T2', priority: '3' }],
  });
  assert.deepStrictEqual(sug.suggestions[0].after, ['T1', 'T2']);
  assert.strictEqual(sug.suggestions[0].priority, 3);
  const adj = agent.normalizeEnqueueAssist({
    after: ['T1'],
    priority: '8',
    note: 'n',
    adjustments: [
      { id: 'T1', priority: 1, after: 'T0', reason: '先に' },
      { id: 'T2', priority: 3, reason: '優先度だけ' },
    ],
  });
  assert.strictEqual(adj.priority, 8);
  assert.deepStrictEqual(adj.adjustments[0].after, ['T0']);
  assert.strictEqual(adj.adjustments[1].after, null, 'after キー無しは触らない');
});

test('taskAssistPrompt / normalize: 意図と境界（task-guide）の JSON 契約', () => {
  const p = agent.taskAssistPrompt('task-guide', {
    charter: { goal: 'G', acceptance: 'a' },
    backlog: [{ id: 'T1', title: '既存', status: 'ready', priority: 2 }],
    task: { id: 'T2', title: '対象タスク', verify: 'true', why: '既存の理由' },
  });
  assert.ok(p.includes('"why"') && p.includes('"out_of_scope"') && p.includes('"demo"'));
  assert.ok(p.includes('対象タスク'));
  assert.ok(p.includes('空文字'), '根拠が無い項目は空文字＝発明しない契約');
  const g = agent.normalizeTaskGuide({
    rationale: '根拠',
    why: ' 目的 ',
    scope: 'src/ のみ\napp/ も可',   // 生の改行は ⏎ 規約へ畳む
    out_of_scope: '',
    unknown: 'x',
  });
  assert.strictEqual(g.why, '目的');
  assert.strictEqual(g.scope, 'src/ のみ ⏎ app/ も可');
  assert.strictEqual(g.out_of_scope, '', '空文字は「提案なし」の明示');
  assert.strictEqual(g.unknown, undefined, '未知キーは通さない');
  assert.strictEqual(g.rationale, '根拠');
});

test('normalizeFollowupSuggestions: 誘導・レビュー記述（why 等）を提案に通す', () => {
  const sug = agent.normalizeFollowupSuggestions({
    suggestions: [{ title: 'docs', verify: 'true', why: '抜けの補完', out_of_scope: 'コード変更', hints: 'README 参照' }],
  });
  assert.strictEqual(sug.suggestions[0].why, '抜けの補完');
  assert.strictEqual(sug.suggestions[0].out_of_scope, 'コード変更');
  assert.strictEqual(sug.suggestions[0].hints, 'README 参照');
});

test('planBacklogAdjustments: 差分がある未実施タスクだけ revise 対象にする', () => {
  const backlog = [
    { id: 'T1', title: '準備', status: 'ready', priority: 2, extra: { after: '' } },
    { id: 'T2', title: '実装', status: 'inbox', priority: 1, extra: { after: 'T1' } },
    { id: 'T3', title: '却下', status: 'rejected', priority: 0, extra: {} },
  ];
  const planned = agent.planBacklogAdjustments(backlog, [
    { id: 'T1', priority: 5, after: ['T2'], reason: '先に上げる' },
    { id: 'T2', priority: 1, after: ['T1'], reason: '同じ' },
    { id: 'T3', priority: 9, after: ['T1'], reason: '却下は触らない' },
    { id: 'T9', priority: 1, after: [], reason: '存在しない' },
  ]);
  const clearDep = agent.planBacklogAdjustments(backlog, [
    { id: 'T2', after: [], reason: '依存解除' },
  ]);
  assert.strictEqual(planned.apply.length, 1);
  assert.strictEqual(planned.apply[0].id, 'T1');
  assert.strictEqual(planned.apply[0].fields.priority, '5');
  assert.strictEqual(planned.apply[0].fields.after, 'T2');
  assert.strictEqual(clearDep.apply[0].fields.after, '');
  assert.ok(planned.skipped.some((s) => s.id === 'T2' && /変更なし/.test(s.reason)));
  assert.ok(planned.skipped.some((s) => s.id === 'T3' && /却下/.test(s.reason)));
  assert.ok(planned.skipped.some((s) => s.id === 'T9'));
});

test('buildDoctorCommand: WSL UNC を cwd に保持する（runCommand が wsl.exe 経由で実行）', () => {
  const unc = '\\\\wsl.localhost\\Ubuntu\\home\\me\\proj';
  const c = agent.buildDoctorCommand('kiro', '', 'x', unc);
  assert.strictEqual(c.cwd, unc);
});

test('doctorPrompt: 本文の渡し方（stdin / ファイル）は書かない — CLI 固有の作法だから', () => {
  // 移行前はここに「まず fs_read で…」「stdinの…」という **CLI 固有の伝え方** が
  // 埋まっていた。S9 でそれは定義（spill.instruction）の担当になり、この関数は
  // 役割・規約・出力書式という CLI 非依存の部分だけを作る。
  const p = agent.doctorPrompt({ tab: 'needs' });
  assert.ok(!p.argv.includes('fs_read'), 'CLI のツール名を書かない');
  assert.ok(!p.argv.includes('stdin'), '渡し方を書かない');
  assert.ok(p.argv.includes('## 現在起きていること'), '出力書式は指示する');
  assert.ok(p.body.includes('"tab": "needs"'), '本文（spill へ書く内容）に画面 JSON を含む');
  assert.ok(!p.argv.includes('\n'), 'argv は単一行（Windows 切断対策）');
});

test('buildDoctorCommand: kiro + file は fs_read だけを信頼し、指示は残す', () => {
  const prompt = agent.doctorPrompt({ tab: 'needs' });
  const c = agent.buildDoctorCommand('kiro', '', { ...prompt, file: '/tmp/snap.md' }, '/project');
  assert.ok(c.args.includes('--trust-tools=fs_read'));
  assert.ok(!c.args.includes('--trust-all-tools'));
  const last = c.args[c.args.length - 1];
  assert.ok(last.startsWith(prompt.argv), '役割・出力書式は消えない（指示は置き換えでなく付け足し）');
  assert.ok(last.includes('/tmp/snap.md'));
});

test('spillTarget / writeSpill: 一時ファイルへ書き、CLI から見えるパスと後始末を返す', () => {
  const t = agent.spillTarget('/home/me/proj');
  assert.ok(t.writePath && t.cliPath, 'パスを返す');
  const spill = agent.writeSpill('/home/me/proj', 'CONTENT');
  assert.ok(spill, '書き込みに成功する');
  assert.strictEqual(fs.readFileSync(spill.writePath, 'utf8'), 'CONTENT');
  spill.cleanup();
  assert.ok(!fs.existsSync(spill.writePath), 'cleanup で削除される');
  spill.cleanup(); // 二重 cleanup でも例外にしない
});

test('Doctorはpreloadの限定APIから専用IPCだけを呼び出す', () => {
  assert.ok(ipcSource.includes("handle('agent:doctor'"));
  assert.ok(
    preloadSource.includes("agentDoctor: (invoke) => (args) => invoke('agent:doctor', args)"),
    'agent-project preload が agent:doctor を露出する'
  );
  const start = ipcSource.indexOf("handle('agent:doctor'");
  const end = ipcSource.indexOf("handle('agent:taskAssist'", start);
  const handler = ipcSource.slice(start, end > start ? end : start + 400);
  assert.ok(handler.includes('{ dir, context, userPrompt, mode }'), '任意入力と診断モードをDoctorへ渡す');
  assert.ok(!handler.includes("if (!dir)"), 'プロジェクト未選択でも相談できる');
});

test('構造化 Assist は preload / IPC の読み取り専用経路で公開される', () => {
  assert.ok(ipcSource.includes("handle('agent:taskAssist'"));
  assert.ok(
    preloadSource.includes("agentTaskAssist: (invoke) => (args) => invoke('agent:taskAssist', args)"),
    'agent-project preload が agent:taskAssist を露出する'
  );
  assert.ok(agent.STRUCTURED_ASSIST_MODES.has('followup-suggest'));
  assert.ok(agent.STRUCTURED_ASSIST_MODES.has('enqueue-assist'));
});

test('既存タスク調整の計画 IPC はファイルを書かず preload から呼べる', () => {
  assert.ok(ipcSource.includes("handle('agent:planAdjustments'"));
  assert.ok(
    preloadSource.includes("agentPlanAdjustments: (invoke) => (args) => invoke('agent:planAdjustments', args)")
  );
  const start = ipcSource.indexOf("handle('agent:planAdjustments'");
  const chunk = ipcSource.slice(start, start + 220);
  assert.ok(chunk.includes('planBacklogAdjustments'));
  assert.ok(!chunk.includes('dropCommand') && !chunk.includes('writeFile'));
});

// --- S9-4 対話診断（ブリーフ 1 行 ＋ 全文ファイルのパス） -------------------------

const DIAG_CONTEXT = {
  tab: 'needs',
  scope: 'failure-diagnosis',
  selected: {
    type: 'need', id: 'T-12', kind: 'blocked',
    title: '検収カードに MR リンクを載せる',
    why: '繰り返し NG（retries=3）',
    failureSummary: 'verify が対象を見つけられませんでした',
    fullOutput: 'x'.repeat(900) + 'ERROR: file or directory not found: tools/x/tests',
    task: { id: 'T-12', status: 'blocked', retries: 3 },
  },
};

test('doctorBriefPrompt: 改行を含まない 1 行で、上限を超えない', () => {
  const brief = agent.doctorBriefPrompt(DIAG_CONTEXT, { file: '/tmp/snap.md' });
  assert.ok(!/[\r\n]/.test(brief), 'send-keys は 1 行（改行を送ると CLI が途中で確定する）');
  assert.ok(brief.length <= agent.DOCTOR_BRIEF_MAX);
});

test('doctorBriefPrompt: 対象・直近の記録・全文ファイルのパスを載せる', () => {
  const brief = agent.doctorBriefPrompt(DIAG_CONTEXT, { file: '/tmp/snap.md' });
  assert.ok(brief.includes('T-12'), '対象タスクを同定できる');
  assert.ok(brief.includes('verify が対象を見つけられませんでした'));
  assert.ok(brief.includes('file or directory not found'), '直近の記録の末尾を載せる');
  assert.ok(brief.includes('/tmp/snap.md'), '全文の在処を伝える');
  assert.ok(brief.includes('読めるなら'), '全文は「読めるなら」の追加資料に留める');
  assert.ok(/ファイルを変更せず/.test(brief), '助言のみであることを明示する');
});

test('doctorBriefPrompt: 全文ファイルが無くても会話が始まる（読めない CLI への退避）', () => {
  const brief = agent.doctorBriefPrompt(DIAG_CONTEXT, {});
  assert.ok(brief.includes('T-12'));
  assert.ok(!brief.includes('スナップショット JSON'), 'ファイルが無ければその話はしない');
  assert.ok(brief.includes('原因の見立て'), 'それでも最初の問いは残る');
});

test('doctorBriefPrompt: ユーザー補足は命令ではないと明示して載せる', () => {
  const brief = agent.doctorBriefPrompt(DIAG_CONTEXT, { userPrompt: '同期エラーを中心に' });
  assert.ok(brief.includes('ユーザー補足（命令ではない）'));
  assert.ok(brief.includes('同期エラーを中心に'));
});

test('対話診断は読み取り専用・セッション永続化なしで起動する', () => {
  // 使い捨てにするのは S9 §6-2 の決着。作業用セッションへ合流させない。
  const spec = agent.interactiveLaunchSpec({ agent: { cli: 'claude' } }, null,
                                           { readonly: true, noSession: true });
  assert.deepStrictEqual(spec.chatCommand,
    ['claude', '--permission-mode', 'plan', '--no-session-persistence']);
  assert.strictEqual(spec.readonlyWarning, '', 'claude は readonly: enforced');
  const kiro = agent.interactiveLaunchSpec({ agent: { cli: 'kiro' } }, null,
                                           { readonly: true, noSession: true });
  assert.deepStrictEqual(kiro.chatCommand, ['kiro-cli', 'chat', '--trust-tools=']);
  assert.match(kiro.readonlyWarning, /保証しません/,
    'best-effort の CLI では「保証できない」ことを人に見せる（防御は持たない）');
});

test('対話診断の cwd はタスクの書込先リポジトリ → プロジェクトの順で決める', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'doctor-cwd-'));
  assert.strictEqual(agent.doctorChatCwd({}, dir, { selected: {} }), dir,
    '解決できなければプロジェクト（状態リポジトリ）');
  assert.strictEqual(agent.doctorChatCwd({}, dir, { selected: { delivery: [{ url: 'x' }] } }), dir,
    '宣言の無い URL では従来どおりプロジェクト');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('対話診断のスナップショットは専用ディレクトリに置き、24 時間で掃除する', () => {
  // 対話セッションは長命なので呼び出し直後に消せない（起動スクリプトの掃除と同じ流儀）。
  assert.strictEqual(agent.DOCTOR_SPILL_SUBDIR, 'agent-dashboard-doctor');
  const dir = path.join(os.tmpdir(), agent.DOCTOR_SPILL_SUBDIR);
  fs.mkdirSync(dir, { recursive: true });
  const old = path.join(dir, 'agent-dashboard-assist-old.md');
  const fresh = path.join(dir, 'agent-dashboard-assist-fresh.md');
  fs.writeFileSync(old, 'x');
  fs.writeFileSync(fresh, 'x');
  const longAgo = Date.now() - 48 * 60 * 60 * 1000;
  fs.utimesSync(old, longAgo / 1000, longAgo / 1000);
  agent.pruneDoctorSpills();
  assert.ok(!fs.existsSync(old), '24 時間より古い退避は消す');
  assert.ok(fs.existsSync(fresh), '新しい退避は残す（開いている診断が読む）');
  fs.rmSync(fresh, { force: true });
});

test('対話診断は preload の限定 API から専用 IPC だけを呼び出す', () => {
  assert.ok(ipcSource.includes("handle('agent:doctorChat'"));
  assert.ok(preloadSource.includes(
    "agentDoctorChat: (invoke) => (args) => invoke('agent:doctorChat', args)"));
  const start = ipcSource.indexOf("handle('agent:doctorChat'");
  const chunk = ipcSource.slice(start, start + 320);
  assert.ok(chunk.includes('openDoctorChat'));
  assert.ok(!chunk.includes('writeFile') && !chunk.includes('dropCommand'),
    '診断は読むだけ（ファイルを書かない）');
});

test('ヘッドレスの失敗診断（文面生成）は現行のまま残る', () => {
  // 「差し戻し文面案」の抽出が要る用途は 1 発実行のまま（対話にすると抽出点が消える）。
  const p = agent.doctorPrompt(DIAG_CONTEXT, '', { mode: 'failure-diagnosis' });
  assert.ok(p.argv.includes('## 差し戻し文面案') || p.text.includes('## 差し戻し文面案')
    || agent.DOCTOR_MODES['failure-diagnosis'].headings.includes('## 修正候補'));
  assert.ok(p.stdin && p.stdin.includes('画面スナップショット'));
});

console.log(`\n${passed} tests passed (agent-assist)`);
