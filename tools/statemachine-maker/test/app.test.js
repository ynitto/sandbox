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

test('画面はライトトーンで、色・余白・文字は CSS 変数（設定とユーザー CSS が上書きできる）', () => {
  const css = read('renderer/styles.css');
  assert.match(css, /--bg: #f7f7f8/);
  assert.match(css, /--card: #ffffff/);
  for (const v of ['--accent', '--space', '--card-pad', '--font-size', '--column-width', '--kind-browser', '--kind-agent']) assert.ok(css.includes(`${v}:`), v);
  assert.ok(read('main/main.js').includes("backgroundColor: '#f7f7f8'"));
  assert.ok(read('renderer/index.html').includes('id="custom-css"'), 'ユーザー CSS の差し込み口');
  const theme = require('../src/main/theme');
  const vars = theme.cssVariables({ accent: '#112233', density: 'compact', fontSize: 16, kindColors: { agent: '#445566', bogus: '#000000' } });
  assert.strictEqual(vars['--accent'], '#112233');
  assert.strictEqual(vars['--font-size'], '16px');
  assert.strictEqual(vars['--space'], '8px');
  assert.strictEqual(vars['--kind-agent'], '#445566');
  assert.strictEqual(vars['--kind-browser'], theme.DEFAULTS.kindColors.browser, '不正な値・未知の種類は既定に戻る');
  assert.strictEqual(theme.normalize({ accent: 'red', fontSize: 99 }).accent, theme.DEFAULTS.accent);
  const dir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'smk-theme-'));
  assert.strictEqual(theme.load(dir).customCss, '');
  theme.ensureCustomCss(dir);
  assert.ok(theme.load(dir).customCss.includes('--accent'), '雛形は変数の名前を教える');
});

test('編集画面は 1 列: 工程カードがその場で開き、カードの間に遷移、右ペインとタブを持たない', () => {
  const renderer = read('renderer/renderer.js');
  const html = read('renderer/index.html');
  assert.ok(renderer.includes('step-head') && renderer.includes('step-body') && renderer.includes('class="edge"'));
  assert.ok(!html.includes('id="inspector"') && !html.includes('id="tabs"'));
  assert.ok(/<dialog id="dlg-(record|files|ai|run|settings)"/.test(html), '補助機能はダイアログ');
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
