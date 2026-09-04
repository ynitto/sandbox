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

test('renderer が呼ぶ api.* は preload にあり、preload のチャネルは ipc.js が受ける', () => {
  const preload = read('preload.js');
  const renderer = read('renderer/renderer.js');
  const ipc = read('main/ipc.js');
  const exposed = new Set([...preload.matchAll(/^\s{2}(\w+): /gm)].map((m) => m[1]));
  const used = new Set([...renderer.matchAll(/\bapi\.(\w+)\(/g)].map((m) => m[1]));
  for (const name of used) assert.ok(exposed.has(name), `preload に無い api: ${name}`);
  const channels = [...preload.matchAll(/invoke\('([\w:]+)'/g)].map((m) => m[1]);
  for (const ch of channels) assert.ok(ipc.includes(`handle('${ch}'`), `ipc.js が受けないチャネル: ${ch}`);
  assert.ok(read('main/main.js').includes('contextIsolation: true') && read('main/main.js').includes('sandbox: true'));
  assert.ok(read('renderer/index.html').includes("script-src 'self'"));
});

test('画面はライトトーン（地は白系、意味のある色だけ）', () => {
  const css = read('renderer/styles.css');
  assert.match(css, /--bg: #f6f7f9/);
  assert.match(css, /--bg2: #ffffff/);
  assert.ok(read('main/main.js').includes("backgroundColor: '#f6f7f9'"));
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
  assert.ok(text.includes('NG → 工程 1 へ戻る（step_1）'));
  assert.ok(!text.includes('initial_state:') && !text.includes('states:') && !text.includes('transitions:'));
  const prompt = instruction.creationPrompt(spec, { machineDir: '.statemachine/kintai/' });
  assert.ok(prompt.startsWith('statemachine-use スキルの作成モードで'));
  assert.ok(prompt.includes('.statemachine/kintai/'));
});

test('設定はユーザーデータの 1 ファイルで、最近のフォルダは重複せず 10 件まで', () => {
  const dir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'smk-cfg-'));
  assert.deepStrictEqual(config.load(dir), config.DEFAULTS);
  for (let i = 0; i < 12; i += 1) config.remember(dir, `/r/${i}`);
  config.remember(dir, '/r/11');
  const cfg = config.load(dir);
  assert.strictEqual(cfg.recentRoots.length, 10);
  assert.strictEqual(cfg.recentRoots[0], '/r/11');
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
