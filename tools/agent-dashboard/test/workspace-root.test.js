'use strict';

// ワークスペース → プロジェクトルートの解決を検証する。追加依存なしで
// `node test/workspace-root.test.js` で走る。
//
//   ワークスペース  … このビュアーに登録するフォルダ。.agent/agent-project.yaml を持つ開発フォルダ
//   プロジェクトルート … 設定 `root:` が指す状態の置き場（backlog / needs / charter / bus の親）
//
// 登録がワークスペースでも、状態フォルダ直指定（従来・instances 由来）でも同じように読めること。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkWorkspace({ root = '.agent-project', configDir = '.agent' } = {}) {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-ws-'));
  fs.mkdirSync(path.join(ws, configDir), { recursive: true });
  fs.writeFileSync(
    path.join(ws, configDir, 'agent-project.yaml'),
    `# 設定\nroot: ${root}\nplanner: none\nexecutor: stub\n`,
    'utf8'
  );
  const state = path.join(ws, root);
  fs.mkdirSync(path.join(state, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(state, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  fs.writeFileSync(
    path.join(state, 'backlog', 'T1.md'),
    '## T1: やること\n- status: ready\n- verify: `true`\n',
    'utf8'
  );
  return { ws, state };
}

test('resolveProjectRoot は .agent/agent-project.yaml の root: を解決する', () => {
  const { ws, state } = mkWorkspace();
  try {
    assert.strictEqual(project.resolveProjectRoot(ws), path.resolve(state));
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('resolveProjectRoot はワークスペース直下の agent-project.yaml も見る（本体の探索順と同じ）', () => {
  const { ws, state } = mkWorkspace({ configDir: '.' });
  try {
    assert.strictEqual(project.resolveProjectRoot(ws), path.resolve(state));
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('root: が無ければワークスペース自身がプロジェクトルート（状態フォルダ直指定の従来構成）', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-ws-'));
  try {
    fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
    assert.strictEqual(project.resolveProjectRoot(dir), path.resolve(dir));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('readProject はワークスペースを受け、状態はプロジェクトルートから読む', () => {
  const { ws, state } = mkWorkspace();
  try {
    const p = project.readProject(ws, { projects: {} });
    assert.strictEqual(p.workspace, path.resolve(ws));
    assert.strictEqual(p.dir, path.resolve(state), 'dir はプロジェクトルート（操作の基準）');
    assert.strictEqual(p.name, path.basename(ws), '表示名はワークスペース名');
    assert.strictEqual(p.backlog.length, 1, 'backlog はプロジェクトルートから読む');
    assert.strictEqual(p.charter.name, 'demo');
    // バスの既定はプロジェクトルート直下（本体の <root>/bus と一致）
    assert.strictEqual(p.busDir, path.join(path.resolve(state), 'bus'));
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('discover はワークスペースを 1 件として並べ、状態はプロジェクトルートから数える', () => {
  const { ws, state } = mkWorkspace();
  try {
    const { projects } = project.discover({ projects: { roots: [ws], autoDiscover: false } });
    const p = projects.find((x) => x.dir === path.resolve(ws));
    assert.ok(p, '登録したワークスペースが 1 件として出る');
    assert.strictEqual(p.root, path.resolve(state), 'root は状態の置き場');
    assert.strictEqual(p.name, path.basename(ws));
    assert.strictEqual(p.backlogCount, 1);
    assert.strictEqual(p.charterName, 'demo');
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('~/.agent のグローバル設定の root: は使わない（全ワークスペースが同じ状態を指してしまう）', () => {
  // ワークスペース側に設定が無ければ、~/.kiro に root: があっても自分自身を返す。
  // readToolConfig は ~/.kiro をフォールバックに含むため、明示的に守る必要がある。
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-ws-'));
  try {
    fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
    assert.strictEqual(project.resolveProjectRoot(dir), path.resolve(dir));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
