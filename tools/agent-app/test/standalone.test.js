'use strict';

// agent-tools（agent-herd / agent-loop / agent-flow）が無い PC でもメイン機能（会話・タスクの
// 作成と実行）が動くこと。一覧は自前、AI 支援は定義から組んだ単発 argv、手動実行は同梱スキルの
// run_machine.py（exec バックエンド）で回す。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');
const agents = require('../src/main/agents');
const agentCli = require('../src/main/agentCli');
const directRun = require('../src/main/automation/direct-run');
const automationIpc = require('../src/main/automation/ipc');
const tools = require('../src/main/automation/tools');
const runner = require('../src/main/automation/runner');
const ai = require('../src/main/automation/ai');

const SRC = path.join(__dirname, '..', 'src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8');

// ホストのシェルの代わり: PATH に居ることにする名前の並び
function fakeShell(present) {
  return () => ({
    run: async (script) => {
      const names = [...script.matchAll(/'([^']+)'/g)].map((m) => m[1]);
      return { ok: true, output: names.map((n) => `${n}=${present.includes(n) ? `/usr/bin/${n}` : ''}`).join('\n') };
    },
  });
}

test('一覧: 会話とタスクは同じ 1 つの一覧を見る。使えるものだけが名前の並びになり、一族が使えれば herd も並ぶ', async () => {
  const withoutHerd = await agents.listAgents('', { distro: 'x1', shellFor: fakeShell(['claude', 'codex']) });
  assert.deepStrictEqual(agents.usableNames(withoutHerd).sort(), ['claude', 'codex']);
  assert.ok(withoutHerd.some((e) => e.name === 'herd' && !e.available), 'herd の行は一族の定義があれば出るが使えない印');
  const withHerd = await agents.listAgents('', { distro: 'x2', shellFor: fakeShell(['claude', 'agent-herd']) });
  assert.deepStrictEqual(agents.usableNames(withHerd).sort(), ['aider', 'claude', 'herd', 'ollama']);
});

test('単発 argv: セッション継続も履歴も持たず、プロンプトの渡し方だけを返す', () => {
  const claude = agentCli.oneShotCmd(agentCli.load('claude', ''), { readonly: true });
  assert.deepStrictEqual(claude.argv, ['claude', '-p', '--output-format', 'text', '--permission-mode', 'plan']);
  assert.strictEqual(claude.promptVia, 'stdin');
  assert.ok(!claude.argv.some((t) => t.includes('--session-id')), 'セッション ID は発行しない');
  const copilot = agentCli.oneShotCmd(agentCli.load('copilot', ''), { model: 'gpt-5' });
  assert.strictEqual(copilot.promptVia, 'argv');
  assert.deepStrictEqual(copilot.argv.slice(-3), ['--model', 'gpt-5', '-p'], 'argv 渡しの prompt_flag は最後に置き、本文はその後ろ');
  const codex = agentCli.oneShotCmd(agentCli.load('codex', ''), {});
  assert.ok(codex.argv.includes('{output_file}') && codex.outputFile, '{output_file} は起こす側が置き換える');
});

test('AI 支援: herd 以外は定義から組んだ単発 argv でその CLI を直接起こす（agent-herd を経由しない）', () => {
  const spec = automationIpc.assistRunSpec({ root: '/r', agent: 'claude', prompt: 'JSON だけ' });
  assert.strictEqual(spec.command, 'claude');
  assert.deepStrictEqual(spec.args, ['-p', '--output-format', 'text', '--permission-mode', 'plan']);
  assert.strictEqual(spec.input, 'JSON だけ', 'stdin 渡しの定義は本文を input に');
  assert.strictEqual(spec.host, true, 'Windows では WSL 側で起こす');
  assert.strictEqual(typeof spec.extract, 'function');
  const copilot = automationIpc.assistRunSpec({ root: '/r', agent: 'copilot', prompt: 'JSON だけ' });
  assert.strictEqual(copilot.args[copilot.args.length - 1], 'JSON だけ', 'argv 渡しの定義は本文を最後の引数に');
  assert.strictEqual(copilot.input, '');
  const codex = automationIpc.assistRunSpec({ root: '/r', agent: 'codex', prompt: 'x' });
  assert.ok(codex.outputFile && !codex.args.includes('{output_file}'), '{output_file} は一時ファイルへ置き換える');
  assert.ok(fs.existsSync(path.dirname(codex.outputFile)));
});

test('AI 支援: 直接起こした CLI の応答を単発で受け取る（stdin 渡し・偽の CLI）', async (t) => {
  if (process.platform === 'win32') { t.skip('POSIX の実行ファイルを使う試験'); return; }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-standalone-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const envelope = {
    schemaVersion: 1, status: 'candidate', summary: '下書き', questions: [], assumptions: [], findings: [],
    candidate: { name: '確認', machine: 'check', purpose: '確認する', steps: [{ kind: 'agent', title: '確認', detail: '内容を確認する' }] },
  };
  // stdin で受けた本文をそのまま echo する CLI（本文が届いていることも確かめる）
  fs.writeFileSync(path.join(dir, 'fakecli'), `#!/usr/bin/env node\nlet s='';process.stdin.on('data',d=>s+=d).on('end',()=>{process.stdout.write(s.trim()===${JSON.stringify('依頼')}?${JSON.stringify(JSON.stringify(envelope))}:'no prompt');});\n`);
  fs.chmodSync(path.join(dir, 'fakecli'), 0o755);
  const exited = await new Promise((resolve, reject) => {
    try {
      runner.stream('fakecli', [], { cwd: dir, kind: 'ai', input: '依頼', env: { ...process.env, PATH: `${dir}${path.delimiter}${process.env.PATH || ''}` }, onExit: resolve });
    } catch (err) { reject(err); }
  });
  assert.strictEqual(exited.code, 0);
  assert.strictEqual(ai.parseEnvelope(exited.stdout, { mode: 'draft' }).candidate.machine, 'check');
});

test('手動実行: agent-loop が無ければ同梱スキルの run_machine.py（exec）に定義の argv を渡して回す', () => {
  const spec = directRun.runSpec({
    root: '/repo', machine: 'nightly', agent: 'claude', model: 'opus', parameters: { input: '依頼', env: 'prod' },
    instruction: '共通指示', skillDir: '/skill', python: 'python3', platform: 'linux',
  });
  assert.strictEqual(spec.command, 'python3');
  assert.strictEqual(spec.host, false);
  assert.deepStrictEqual(spec.args.slice(0, 2), [path.join('/skill', 'scripts', 'run_machine.py'), '.statemachine/nightly/workflow.yaml']);
  assert.ok(spec.args.includes('--result-line'), '結果は RESULT 行で受ける（agent-loop と同じ読み方）');
  const argv = JSON.parse(spec.args[spec.args.indexOf('--agent-command') + 1]);
  assert.deepStrictEqual(argv, ['claude', '-p', '--output-format', 'text', '--dangerously-skip-permissions', '--model', 'opus']);
  assert.deepStrictEqual(spec.args.slice(spec.args.indexOf('--prompt-via'), spec.args.indexOf('--prompt-via') + 2), ['--prompt-via', 'stdin']);
  assert.deepStrictEqual(spec.args.slice(spec.args.indexOf('--instruction'), spec.args.indexOf('--instruction') + 2), ['--instruction', '共通指示']);
  assert.ok(spec.args.includes('--context') && spec.args.includes('env=prod'), '毎回変わる値は --context');
  assert.deepStrictEqual(spec.args.slice(spec.args.indexOf('--input'), spec.args.indexOf('--input') + 2), ['--input', '依頼']);
  assert.throws(() => directRun.runSpec({ root: '/repo', machine: '../x', agent: 'claude', skillDir: '/skill' }), /識別名/);
  assert.throws(() => directRun.runSpec({ root: '/repo', machine: 'a', agent: 'claude', skillDir: '' }), /スキル/);
  // Windows: CLI は WSL に居るので、python も WSL 側（スキルの置き場は WSL の表記へ）
  const win = directRun.runSpec({
    root: 'C:\\repo', machine: 'a', agent: 'claude', skillDir: 'C:\\app\\skill', platform: 'win32',
    hostPath: (v) => `/mnt/c${String(v).replace(/^C:/, '').replace(/\\/g, '/')}`,
  });
  assert.strictEqual(win.command, 'python3');
  assert.strictEqual(win.host, true);
  assert.strictEqual(win.args[0], '/mnt/c/app/skill/scripts/run_machine.py');
});

test('手動実行の配線: inspect が答えないときだけ direct-run へ倒し、prompt のタスクは断る', () => {
  const handlers = read('main/automation/handlers.js');
  assert.match(handlers, /const loopAvailable = snapshot\.available !== false;/);
  assert.match(handlers, /if \(loopAvailable\) \{\s*const spec = agentLoop\.taskRunSpec\(/);
  assert.match(handlers, /if \(task\.kind !== 'statemachine'\) throw new Error\('このタスクの実行には agent-loop が要ります/);
  assert.match(handlers, /directRun\.runSpec\(\{/);
  assert.match(handlers, /host: onHost,/, 'Windows では WSL 側の python で起こす');
  assert.match(handlers, /const assistRunSpec = \(payload\) => \(/, 'AI 支援の起動仕様はフックで差し替える');
  assert.match(handlers, /spec\.outputFile \? readOutputFile\(spec\.outputFile\) : rawStdout/);
  assert.match(handlers, /input: spec\.input \|\| ''/);
  const runnerSrc = read('main/automation/runner.js');
  assert.match(runnerSrc, /child\.stdin\.end\(String\(input == null \? '' : input\)\)/, 'stdin は本文を流して閉じる');
});

test('実行環境: 使える AI は自前の一覧で見て、agent-herd / agent-loop / agent-flow は任意の道具', async () => {
  const capture = async (command, args) => {
    if (command === 'python3') return { ok: true, status: 0, stdout: 'Python 3.13.0', stderr: '' };
    if (command === 'playwright-cli' && args.includes('--version')) return { ok: true, status: 0, stdout: '0.1.18', stderr: '' };
    if (command === 'playwright-cli') return { ok: true, status: 0, stdout: 'recording-start\nrecording-stop\n', stderr: '' };
    return { ok: false, status: 127, stdout: '', stderr: 'not found', error: 'ENOENT' };
  };
  const rows = await tools.toolStatus({ capture, skillDir: '/skill', agentDefinitions: async () => ['claude', 'codex'] });
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.strictEqual(byId.agents.ok, true);
  assert.match(byId.agents.summary, /claude \/ codex/);
  for (const id of ['agent-herd', 'agent-loop', 'agent-flow']) {
    assert.strictEqual(byId[id].ok, false);
    assert.strictEqual(byId[id].optional, true, `${id} は無くても本体が動く`);
    assert.match(byId[id].summary, /無くても/);
  }
  assert.ok(!('agent-tools' in byId), 'agent-herd defs には聞かない');
  const withHerd = await tools.toolStatus({ capture, skillDir: '/skill', agentDefinitions: async () => ['claude', 'herd', 'aider'] });
  assert.strictEqual(withHerd.find((r) => r.id === 'agent-herd').ok, true);
  assert.match(withHerd.find((r) => r.id === 'agents').summary, /2 件/, 'herd は定義の数に数えない');
});

test('画面: agent-loop が無くてもステートマシンのタスクは実行でき、足りないものは 1 行で言う', () => {
  const renderer = read('renderer/automation/renderer.js');
  assert.match(renderer, /\(snapshot\.available === false && machine\.kind !== 'statemachine'\) \|\| !state\.agents\.length/);
  assert.match(renderer, /snapshot\.available === false \? '定期実行と履歴には agent-loop が要ります'/);
  assert.match(renderer, /t\.optional \? \['opt', '任意'\]/);
  assert.ok(!renderer.includes('使う AI（agent-tools）'));
  assert.ok(read('renderer/automation/styles.css').includes('.tool-list .st.opt'));
});

test('既定の AI: 設定が無ければ会話の「おすすめ」と同じ CLI（aider を決め打ちしない）', () => {
  assert.strictEqual(automationIpc.automationConfig({ execution: { tiers: { medium: { cli: 'claude', model: '' } } } }).agent, 'claude');
  assert.strictEqual(automationIpc.automationConfig({ automationAgent: 'codex' }).agent, 'codex');
  assert.strictEqual(automationIpc.automationConfig({ lastCli: 'kiro' }).agent, 'kiro', '旧設定の直接指定も tier へ写った値');
  assert.strictEqual(automationIpc.automationPatch({ agent: '' }).automationAgent, '');
});

test('最適化: 会話は herd の有無と設定で節約 / 品質重視を薄くし、ワークフローと履歴・定期実行も使えなければ薄くする', async () => {
  const renderer = read('renderer/renderer.js');
  assert.match(renderer, /function optimized\(config = state\.config\)/);
  assert.match(renderer, /execution\.optimizeAgents !== false && herdAvailable\(\)/);
  assert.match(renderer, /option\.disabled = !on && !BASIC_POLICIES\.includes\(option\.value\) && option\.value !== 'direct'/, 'ターンごとの起動方針は おすすめ / 直接指定 だけ');
  assert.match(renderer, /\$\('area-workflows'\)\.disabled = !!\(caps && caps\.agentFlow === false\)/, 'agent-flow が無ければワークフローを押せない');
  assert.match(renderer, /const allowed = on \|\| tier === 'medium';/, 'tier は medium だけ');
  assert.match(renderer, /optimizeAgents: \$\('optimize-agents'\)\.checked,/);
  assert.doesNotMatch(renderer, /agent-herd が要ります/, '理由は出さない');
  const html = read('renderer/index.html');
  assert.match(html, /id="optimize-agents"/);
  assert.match(html, /節約・品質重視を使う/);
  const maker = read('renderer/automation/renderer.js');
  assert.match(maker, /return state\.agents\.includes\('herd'\);/);
  assert.match(maker, /policyOn \|\| BASIC_POLICIES\.includes\(value\) \|\| value === 'direct' \? '' : 'disabled'/);
  assert.match(maker, /data-task-tab="history"[^\n]*snapshot\.available === false \? 'disabled' : ''/, 'agent-loop が無ければ履歴タブは押せない');
  assert.match(maker, /execution-card \$\{snapshot\.available === false \? 'is-off' : ''\}/, '定期実行のカードは薄くする');
  assert.match(maker, /id="schedule-toggle" \$\{snapshot\.available === false \? 'disabled' : ''\}/, '予定の追加も押せない');
  const ipc = read('main/ipc.js');
  assert.match(ipc, /settings\.optimized\(cfg, \{ herdAvailable: agentsMod\.herdAvailable\(agents\) \}\)/, 'ターンの解決も同じ規則');
  assert.match(read('preload.js'), /invoke\('automation:capabilities'/);
  // 道具の有無は 1 つの問い合わせ（60 秒キャッシュ）
  const calls = [];
  const capture = async (command) => { calls.push(command); return { ok: command === 'agent-loop', stdout: '', stderr: '' }; };
  let clock = 0;
  const caps = await tools.capabilities({ cwd: '/r', capture, agentDefinitions: async () => ['claude', 'herd'], flowAvailable: async () => false, now: () => clock });
  assert.deepStrictEqual(caps, { herd: true, agentLoop: true, agentFlow: false });
  clock = 1000;
  await tools.capabilities({ cwd: '/r', capture, agentDefinitions: async () => [], flowAvailable: async () => true, now: () => clock });
  assert.strictEqual(calls.length, 1, '60 秒以内は起動し直さない');
  assert.strictEqual(agents.herdAvailable([{ name: 'herd', virtual: true, available: true }]), true);
  assert.strictEqual(agents.herdAvailable([{ name: 'herd', virtual: true, available: false }, { name: 'claude', available: true }]), false);
});
