'use strict';

// agent.js（エージェント CLI 連携・charter 補完層）の軽量テスト。追加依存なしで
// `node test/agent-assist.test.js` で走る。CLI の実行（spawn）はしない —
// コマンド組み立て・設定解決・応答パースの純関数だけを検証する。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agent = require('../src/main/agent');
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

test('buildDoctorCommand: kiro はツールを一切許可せず、短い argv + stdin で助言する', () => {
  const prompt = agent.doctorPrompt({ tab: 'needs' });
  const c = agent.buildDoctorCommand('kiro', '', prompt, '/project');
  assert.strictEqual(c.command, 'kiro-cli');
  assert.ok(c.args.includes('--trust-tools='));
  assert.ok(!c.args.includes('--trust-all-tools'));
  assert.strictEqual(c.args[c.args.length - 1], prompt.argv);
  assert.ok(!c.args[c.args.length - 1].includes('\n'), 'argv は単一行（Windows 切断対策）');
  assert.strictEqual(c.stdin, prompt.stdin);
  assert.ok(c.stdin.includes('"tab": "needs"'), '画面 JSON は stdin');
});

test('buildDoctorCommand: 文字列 prompt でも後方互換で動く', () => {
  const c = agent.buildDoctorCommand('kiro', '', 'CONTEXT', '/project');
  assert.strictEqual(c.args[c.args.length - 1], 'CONTEXT');
  assert.strictEqual(c.stdin, null);
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

test('buildDoctorCommand: WSL UNC を cwd にしない（Windows ネイティブ CLI 対策）', () => {
  const unc = '\\\\wsl.localhost\\Ubuntu\\home\\me\\proj';
  const c = agent.buildDoctorCommand('kiro', '', 'x', unc);
  if (process.platform === 'win32') {
    assert.strictEqual(c.cwd, null);
  } else {
    assert.strictEqual(c.cwd, unc);
  }
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

console.log(`\n${passed} tests passed (agent-assist)`);
