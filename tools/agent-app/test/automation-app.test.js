'use strict';

// タスク・ワークフローの共有ワークベンチ（旧 statemachine-maker）の形を、Electron を起動せずに固定する:
// 構文・画面の言葉・IPC の境界・agent-app の中で動く前提。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const tools = require('../src/main/automation/tools');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

test('共有ワークベンチの main / renderer は構文検査を通る', () => {
  for (const f of ['main/automation/handlers.js', 'main/automation/ipc.js', 'main/automation/teaching.js', 'main/automation/flow-model.js', 'main/automation/flow-store.js', 'main/automation/agent-flow.js', 'main/automation/flow-teaching-model.js', 'main/automation/flow-teaching-store.js', 'renderer/automation/flow.js', 'renderer/automation/teaching.js', 'renderer/automation/renderer.js', 'renderer/automation/workbench-element.js']) {
    execFileSync(process.execPath, ['--check', path.join(SRC, f)]);
  }
  assert.ok(!fs.existsSync(path.join(SRC, 'renderer', 'vendor', 'statemachine')), '共有 renderer は vendor へ写さず、自分のソースとして持つ');
  assert.ok(!fs.existsSync(path.join(__dirname, '..', '..', 'statemachine-maker')), '独立版 statemachine-maker は残さない（agent-app に統合した）');
});

test('画面は固定デザインで、見た目のカスタマイズを公開しない', () => {
  const css = read('renderer/automation/styles.css');
  assert.match(css, /--bg: #f6f7f9/);
  assert.match(css, /--primary: #2563eb/);
  const sources = [read('renderer/automation/renderer.js'), read('preload.js'), read('main/automation/handlers.js')].join('\n');
  for (const term of ['custom-css', 'getTheme', 'saveTheme', 'openCustomCss', 'theme:get', 'theme:save', 'theme:openCss']) {
    assert.ok(!sources.includes(term), `見た目設定の公開面が残っています: ${term}`);
  }
});

test('AIワークフローは既存のカードと2カラムを使い、編集・実行・回答・成果を一続きに扱う', () => {
  const renderer = read('renderer/automation/renderer.js');
  const flow = read('renderer/automation/flow.js');
  const css = read('renderer/automation/styles.css');
  assert.ok(renderer.includes('window.createFlowFeature('));
  for (const action of ['data-flow-new', 'data-flow-edit', 'data-flow-start', 'data-flow-answer', 'data-flow-result', 'data-flow-open-delivery']) {
    assert.ok(flow.includes(action), action);
  }
  assert.match(flow, /execution-layout flow-layout/);
  assert.match(css, /\.flow-layout\s*\{[^}]*grid-template-columns:/);
  assert.ok(flow.includes('AIワークフロー') && flow.includes('読み取り専用で実行する'));
});

test('標準パターンは動的工程を一枚で示し、反復設定を適用後も保持する', () => {
  const flow = read('renderer/automation/flow.js');
  const css = read('renderer/automation/styles.css');
  assert.match(flow, /function workflowStages\(/);
  assert.match(flow, /kind === 'classify'[\s\S]*分類結果に応じて実行/);
  assert.match(flow, /kind === 'split'[\s\S]*要素ごとに実行[\s\S]*結果を集約/);
  assert.match(flow, /rework:\s*Array\.isArray\(pattern\.template\.rework\)/);
  assert.match(css, /\.flow-node-card\.is-dynamic/);
});

test('編集画面は左のフローと右の編集パネルを分離し、狭い画面では一方だけを表示する', () => {
  const renderer = read('renderer/automation/renderer.js');
  const css = read('renderer/automation/styles.css');
  assert.match(renderer, /<div class="editor-shell[^"]*">[\s\S]*<section class="flow-pane">[\s\S]*<aside class="inspector"/);
  assert.ok(renderer.includes('function inspectorHtml('));
  assert.match(css, /\.editor-shell\s*\{[^}]*grid-template-columns:\s*minmax\(360px, 1fr\) 400px/);
  assert.match(css, /@media \(max-width: 899px\)[\s\S]*\.editor-shell\.is-inspecting \.flow-pane\s*\{\s*display:\s*none/);
});

test('主要操作と工程設定は省略語や直訳調の文言を使わない', () => {
  const renderer = read('renderer/automation/renderer.js');
  for (const label of ['操作を記録', 'テスト・実行', '生成ファイル', 'AIで見直す', 'AIで下書き', '実行環境', '実行方法', '工程名', '次の工程', '回答が指定の言葉で始まる', '条件に当てはまる', '詳細条件', '構成を確認']) {
    assert.ok(renderer.includes(label), `表示文言がありません: ${label}`);
  }
  assert.ok(renderer.includes('class="more-menu"'), '補助操作は「その他」にまとめる');
  assert.ok(renderer.includes('class="branch-if">もし') && renderer.includes('class="branch-then">なら'), '条件を文章として読める');
});

test('手動実行は選択したスキルを実行情報へ残す', () => {
  const renderer = read('renderer/automation/renderer.js');
  const handlers = read('main/automation/handlers.js');
  assert.ok(handlers.includes('executionInformation:'));
  assert.ok(handlers.includes('skillSelection:'));
  assert.ok(renderer.includes('適用スキル:'));
});

test('AI支援は下書きと見直しを分け、候補を保存せず選択反映する', () => {
  const renderer = read('renderer/automation/renderer.js');
  const preload = read('preload.js');
  const handlers = read('main/automation/handlers.js');
  assert.ok(renderer.includes("dialog('dlg-ai-draft', 'AIで下書き'") && renderer.includes("dialog('dlg-ai', 'AIで見直す'"));
  assert.ok(renderer.includes('data-ai-answer') && renderer.includes('data-ai-change'));
  assert.ok(preload.includes("invoke('automation:ai:start'") && preload.includes("invoke('automation:ai:apply'"));
  assert.ok(handlers.includes("register('ai:start'") && handlers.includes("register('ai:apply'"));
  assert.ok(handlers.includes('tools.agentAssistRunSpec(') && handlers.includes('aiDiff.apply('));
});

test('タスクの作成・変更は AI との tmux 会話で行い、構造化した教示の往復を持たない', () => {
  const handlers = read('main/automation/handlers.js');
  const ai = read('main/automation/ai.js');
  const teachingUi = read('renderer/automation/teaching.js');
  assert.ok(!handlers.includes("p.mode === 'teach'") && !ai.includes('parseTeachingEnvelope'), 'タスクの教示を JSON の往復で受けない');
  assert.ok(handlers.includes("register('teaching:list'"), '定義がまだ無い下書きは一覧に出す');
  for (const channel of ['teaching:create', 'teaching:stage', 'teaching:trial', 'teaching:confirm']) {
    assert.ok(!handlers.includes(`register('${channel}'`), `試運転・承認の往復は残さない: ${channel}`);
  }
  assert.match(teachingUi, /<slot name="teaching"><\/slot>/, '会話の実体は親（agent-app）が slot に載せる');
  assert.match(teachingUi, /statemachine:teaching-view|ctx\.view\(/);
  assert.ok(!teachingUi.includes('試運転待ち') && !teachingUi.includes('確認待ち'), '状態語は 利用可能 / 下書き の 2 つ');
  for (const label of ['利用可能', '下書き']) assert.ok(teachingUi.includes(label), label);
});

test('定義があるタスクは実行詳細から開き、AI との会話は「AIに変更を相談」で開く', () => {
  const renderer = read('renderer/automation/renderer.js');
  assert.ok(!renderer.includes('!!selectedTask.machine'), '既存定義を一律に会話へ送らない');
  assert.match(renderer, /payload\.action === 'teach'/);
  assert.ok(renderer.includes('data-run-teach') && renderer.includes('AIに変更を相談'));
  assert.ok(renderer.includes('teachingFeature.statusOf('), '実行詳細の状態は教示側の判定を使う');
  assert.match(renderer, /function taskDetailShellHtml\(/);
  assert.match(renderer, /data-task-tab="overview"[\s\S]*data-task-tab="steps"[\s\S]*data-task-tab="teach"[\s\S]*data-task-tab="history"/);
  assert.match(renderer, /teachingFeature\.detailHtml\(\)/);
  assert.match(renderer, /setController\(\{ navigate: navigateEmbedded, refresh: refreshEmbedded \}\)/, '親の会話が終わったら定義を読み直せる');
});

test('使うAIの候補と実行は agent-tools の公開インターフェースに従う', () => {
  const preload = read('preload.js');
  const renderer = read('renderer/automation/renderer.js');
  const handlers = read('main/automation/handlers.js');
  assert.ok(preload.includes("invoke('automation:agents:list'"), 'agent-tools の定義一覧を公開する');
  assert.ok(renderer.includes('automationHost.listAgents('), '画面は preload の窓口から定義一覧を取得する');
  assert.ok(!renderer.includes("['claude', 'copilot', 'kiro', 'anthropic']"), 'AI名を画面へ直書きしない');
  assert.ok(handlers.includes("register('agents:list'"), 'main が定義一覧を返す');
  assert.ok(handlers.includes('agentLoop.taskRunSpec('), '実行はタスク種別に応じて agent-loop へ渡す');
});

test('登録していないフォルダは触らない（main が断る）', () => {
  const handlers = read('main/automation/handlers.js');
  assert.match(handlers, /settings\.isRegistered\(/, 'requireRoot が注入された設定で登録を確かめること');
  for (const channel of ['machine:list', 'machine:read', 'machine:save', 'machine:openFolder']) {
    assert.ok(handlers.includes(`register('${channel}'`), `${channel} が無い`);
  }
  assert.ok(!handlers.includes("register('workflow:choose'"), '任意のファイルを開く口は持たない');
});

// 画面に出す言葉に内部の綴りを混ぜない（コメントは対象外）。
test('画面の言葉に内部の用語が漏れていない', () => {
  const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.replace(/(^|\s)\/\/.*$/, '')).join('\n');
  const source = [read('renderer/automation/renderer.js'), read('renderer/automation/teaching.js'), read('renderer/taskTeaching.js')].map(strip).join('\n');
  const banned = ['output_validator', 'condition_rule', 'check_ok', 'last_output', 'startswith:',
    '--dry-run', '--agent', '--input', '--model', 'run_machine', 'maker.json', 'workflow.yaml',
    '.statemachine', 'statemachine-use', 'YAML', 'ステート ID', '遷移', '終了コード'];
  for (const term of banned) {
    assert.ok(!source.includes(term), `画面の言葉に内部の用語が混ざっています: ${term}`);
  }
});

test('スキルの所在は選んだフォルダから上へ辿って見つける', () => {
  const repo = path.join(__dirname, '..', '..', '..');
  const found = tools.findSkillDir({ root: path.join(repo, 'tools', 'agent-app') });
  if (fs.existsSync(path.join(repo, '.github', 'skills', 'statemachine-use', 'scripts', 'run_machine.py'))) {
    assert.strictEqual(found, path.join(repo, '.github', 'skills', 'statemachine-use'));
  }
  assert.strictEqual(tools.findSkillDir({ root: require('os').tmpdir() }), '');
  assert.strictEqual(tools.findSkillDir({ root: '', appRoot: path.join(__dirname, '..') }), found);
});

// タスク画面の初回表示で固まらない: ホスト（Windows では WSL）に聞くもの（AI の一覧・実行状態）を
// 待ってから描かない。待つのは手元のファイル（定義の一覧・設定）だけ。
test('共有ワークベンチは AI の一覧と実行状態を待たずに描き、届いたら描き直す', () => {
  const renderer = read('renderer/automation/renderer.js');
  const between = (from, to) => renderer.slice(renderer.indexOf(from), renderer.indexOf(to));
  const init = between('async function init()', 'initPromise = init();');
  assert.doesNotMatch(init, /await loadAgents\(\)|await loadExecutionSnapshot\(\)|await Promise\.all\(\[[^\]]*loadAgents/, '初期化は AI の一覧・実行状態を待たない');
  assert.match(init, /\n  loadAgents\(\);\n/, '初期化のあと裏で AI の一覧を取りに行く');
  assert.match(init, /refreshExecutionSnapshot\(\)/);
  const navigate = between('async function navigateEmbedded(', 'async function refreshEmbedded(');
  assert.doesNotMatch(navigate, /await loadAgents\(\)/, 'タスクを開くたびに AI の一覧を待たない');
  const rootChange = between('async function afterRootChange()', 'async function addFolder()');
  assert.doesNotMatch(rootChange, /await loadAgents\(\)|await loadExecutionSnapshot\(\)/, 'リポジトリの切替でも待たない');
  assert.match(rootChange, /await loadMachines\(\);[\s\S]*render\(\);[\s\S]*loadAgents\(\);[\s\S]*refreshExecutionSnapshot\(\);/);
  assert.match(renderer, /let agentsToken = 0;/, '遅れて届いた別リポジトリの返事は捨てる');
  assert.match(renderer, /let snapshotToken = 0;/);
  assert.match(renderer, /function renderIfIdle\(\)/, '入力中に描き直さない');
  assert.match(renderer, /if \(state\.execution\.loading && !machines\.length\) return/, '定義があれば実行状態の到着を待たずに詳細を描く');
  assert.match(renderer, /'実行状態を確認しています…'/);
});
