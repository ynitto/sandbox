'use strict';

// アプリの形: preload が公開する API を renderer が使い、その IPC チャネルを main が受ける。
// Electron を起動せずに、文字列と構文検査で固定する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const instruction = require('../src/main/instruction');
const model = require('../src/main/model');
const config = require('../src/main/config');
const tools = require('../src/main/tools');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

test('main / preload / renderer は構文検査を通る', () => {
  for (const f of ['main/main.js', 'main/ipc.js', 'preload.js', 'renderer/renderer.js']) {
    execFileSync(process.execPath, ['--check', path.join(SRC, f)]);
  }
});

// api.* とチャネルの対応は test/preload-contract.test.js（窓口の契約）が見る。ここは窓の設定だけ。
test('窓の設定は contextIsolation / sandbox を保ち、CSP を宣言する', () => {
  const main = read('main/main.js');
  assert.ok(main.includes('contextIsolation: true') && main.includes('sandbox: true'));
  assert.ok(read('renderer/index.html').includes("script-src 'self'"));
});

test('画面は固定デザインで、見た目のカスタマイズを公開しない', () => {
  const css = read('renderer/styles.css');
  assert.match(css, /--bg: #f6f7f9/);
  assert.match(css, /--surface: #fff(?:fff)?/);
  assert.match(css, /--primary: #2563eb/);
  assert.ok(read('main/main.js').includes("backgroundColor: '#f6f7f9'"));

  const sources = [
    read('renderer/index.html'),
    read('renderer/renderer.js'),
    read('preload.js'),
    read('main/ipc.js'),
  ].join('\n');
  for (const term of ['custom-css', 'getTheme', 'saveTheme', 'openCustomCss', 'theme:get', 'theme:save', 'theme:openCss']) {
    assert.ok(!sources.includes(term), `見た目設定の公開面が残っています: ${term}`);
  }
  assert.ok(!fs.existsSync(path.join(SRC, 'main', 'theme.js')), '見た目設定モジュールを残さない');
});

test('ホームは左のフォルダから右のワークフロー一覧へ読める', () => {
  const renderer = read('renderer/renderer.js');
  const css = read('renderer/styles.css');
  assert.match(renderer, /<div class="home">\s*<aside class="folder-pane">[\s\S]*<section class="machine-pane">/);
  assert.ok(renderer.includes('新しいワークフロー'));
  assert.match(css, /\.home\s*\{[^}]*grid-template-columns:\s*240px minmax\(0, 1fr\)/);
  assert.match(css, /\.folder-pane\s*\{[^}]*border-right:/);
});

test('編集画面は左のフローと右の編集パネルを分離し、狭い画面では一方だけを表示する', () => {
  const renderer = read('renderer/renderer.js');
  const css = read('renderer/styles.css');
  assert.match(renderer, /<div class="editor-shell[^"]*">[\s\S]*<section class="flow-pane">[\s\S]*<aside class="inspector"/);
  assert.ok(renderer.includes('function inspectorHtml('));
  assert.ok(!renderer.includes("${open ? stepBodyHtml(spec, index) : ''}"), '工程カードの中にフォームを展開しない');
  assert.match(css, /\.editor-shell\s*\{[^}]*grid-template-columns:\s*minmax\(360px, 1fr\) 400px/);
  assert.match(css, /@media \(max-width: 899px\)[\s\S]*\.editor-shell\.is-inspecting \.flow-pane\s*\{\s*display:\s*none/);
});

test('主要操作と工程設定は省略語や直訳調の文言を使わない', () => {
  const renderer = read('renderer/renderer.js');
  for (const label of ['操作を記録', 'テスト・実行', '生成ファイル', 'AIで補完', '実行環境', '実行方法', '工程名', '次の工程', '回答が指定の言葉で始まる', '条件に当てはまる', '詳細条件', '構成を確認']) {
    assert.ok(renderer.includes(label), `表示文言がありません: ${label}`);
  }
  for (const oldLabel of ['>記録</button>', '>中身</button>', '>AI</button>', '>動かす</button>', '<label>種類</label>', '<label>名前（任意）</label>', '次にどこへ行くか（任意）']) {
    assert.ok(!renderer.includes(oldLabel), `古い表示文言が残っています: ${oldLabel}`);
  }
  assert.ok(renderer.includes('class="more-menu"'), '補助操作は「その他」にまとめる');
  assert.ok(renderer.includes('class="branch-if">もし') && renderer.includes('class="branch-then">なら'), '条件を文章として読める');
});

test('ダイアログは用途別の幅を持ち、狭い画面で横スクロールを作らない', () => {
  const renderer = read('renderer/renderer.js');
  const css = read('renderer/styles.css');
  for (const [id, size] of [['dlg-record', 'record'], ['dlg-files', 'files'], ['dlg-ai', 'work'], ['dlg-run', 'work'], ['dlg-settings', 'settings']]) {
    assert.match(renderer, new RegExp(`dialog\\('${id}'[^\\n]+, '${size}',`), `${id} の幅指定`);
    assert.ok(css.includes(`.dlg-${size}`), `${size} の幅規則`);
  }
  assert.match(css, /\.dlg-body\s*\{[^}]*overflow-x:\s*hidden/);
  assert.match(css, /@media \(max-width: 719px\)[\s\S]*\.grid2\s*\{\s*grid-template-columns:\s*1fr/);
  assert.match(css, /overflow-wrap:\s*anywhere/);
});

test('作成モードへの指示文は工程・遷移・道具を載せ、YAML の骨組みは書かない', () => {
  const spec = model.normalizeProcedure({ name: '勤怠', machine: 'kintai', purpose: '集計', steps: [
    { kind: 'browser', target: 'https://x', detail: '一覧を読む {{month}}', check: 'python check.py' },
    { kind: 'agent', detail: '判断', outcomes: [{ label: 'OK2', to: 'done' }, { label: 'NG', to: 'step:1' }] },
  ] });
  const text = instruction.creationInstruction(spec, { machineDir: '.statemachine/kintai/' });
  assert.ok(text.includes('## 既存の定義') && text.includes('.statemachine/kintai/'));
  assert.ok(text.includes('`playwright-cli` スキル'));
  assert.ok(text.includes('- {{month}}'));
  assert.ok(text.includes('`check: python check.py`'));
  assert.ok(text.includes('第 1 行が NG で始まる → 工程 1 へ戻る（step_1）'), text);
  assert.ok(!text.includes('initial_state:') && !text.includes('states:') && !text.includes('transitions:'));
  const prompt = instruction.creationPrompt(spec, { machineDir: '.statemachine/kintai/' });
  assert.ok(prompt.startsWith('statemachine-use スキルの作成モードで'));
  assert.ok(prompt.includes('.statemachine/kintai/'));
});

test('設定は登録したフォルダを持ち、旧版の「最近開いたフォルダ」から引き継ぐ', () => {
  const dir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'smk-cfg-'));
  assert.deepStrictEqual(config.load(dir).roots, []);
  assert.strictEqual(config.load(dir).agent, 'aider', '初期値は agent-tools harness の標準定義');
  // 旧版の設定は登録フォルダとして読み替える（作り直させない）
  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify({ recentRoots: ['/a', '/b'] }), 'utf8');
  const migrated = config.load(dir);
  assert.deepStrictEqual(migrated.roots, ['/a', '/b']);
  assert.strictEqual(migrated.lastRoot, '/a');
  assert.ok(!('recentRoots' in migrated), '古い項目は残さない');
  // 登録・解除
  assert.deepStrictEqual(config.addRoot(dir, '/c').roots, ['/a', '/b', '/c']);
  assert.strictEqual(config.load(dir).lastRoot, '/c', '登録したフォルダを開く');
  assert.deepStrictEqual(config.addRoot(dir, '/a').roots, ['/b', '/c', '/a'], '二重に登録しない');
  const dropped = config.removeRoot(dir, '/a');
  assert.deepStrictEqual(dropped.roots, ['/b', '/c']);
  assert.ok(dropped.roots.includes(dropped.lastRoot), '外したフォルダは開いたままにしない');
  assert.ok(config.isRegistered(dir, '/b') && !config.isRegistered(dir, '/a'));
});

test('使うAIの候補と実行は agent-tools の公開インターフェースに従う', () => {
  const preload = read('preload.js');
  const renderer = read('renderer/renderer.js');
  const ipc = read('main/ipc.js');

  assert.ok(preload.includes("invoke('agents:list'"), 'agent-tools の定義一覧を公開する');
  assert.ok(renderer.includes('api.listAgents('), '画面は定義一覧を取得する');
  assert.ok(!renderer.includes("['claude', 'copilot', 'kiro', 'anthropic']"), 'AI名を画面へ直書きしない');
  assert.ok(ipc.includes("handle('agents:list'"), 'main が定義一覧を返す');
  assert.ok(ipc.includes('tools.agentHerdRunSpec('), '実行は agent-herd harness を使う');
});

test('登録していないフォルダは触らない（main が断る）', () => {
  const ipc = read('main/ipc.js');
  assert.match(ipc, /config\.isRegistered\(/, 'requireRoot が登録を確かめること');
  for (const channel of ['machine:list', 'machine:read', 'machine:save', 'machine:openFolder']) {
    assert.ok(ipc.includes(`handle('${channel}'`), `${channel} が無い`);
  }
  assert.ok(!ipc.includes("handle('workflow:choose'"), '任意のファイルを開く口は持たない');
});

// 画面に出す言葉に内部の綴りを混ぜない（コメントは対象外）。
test('画面の言葉に内部の用語が漏れていない', () => {
  const source = read('renderer/renderer.js')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/(^|\s)\/\/.*$/, '')).join('\n');
  const banned = ['output_validator', 'condition_rule', 'check_ok', 'last_output', 'startswith:',
    '--dry-run', '--agent', '--input', '--model', 'run_machine', 'maker.json', 'workflow.yaml',
    '.statemachine', 'statemachine-use', 'YAML', 'ステート ID', '遷移', '終了コード'];
  for (const term of banned) {
    assert.ok(!source.includes(term), `画面の言葉に内部の用語が混ざっています: ${term}`);
  }
});

test('スキルの所在は選んだフォルダから上へ辿って見つける', () => {
  const repo = path.join(__dirname, '..', '..', '..');
  const found = tools.findSkillDir({ root: path.join(repo, 'tools', 'statemachine-maker') });
  if (fs.existsSync(path.join(repo, '.github', 'skills', 'statemachine-use', 'scripts', 'run_machine.py'))) {
    assert.strictEqual(found, path.join(repo, '.github', 'skills', 'statemachine-use'));
  }
  assert.strictEqual(tools.findSkillDir({ root: require('os').tmpdir() }), '');
  assert.strictEqual(tools.findSkillDir({ root: '', appRoot: path.join(__dirname, '..') }), found);
});
