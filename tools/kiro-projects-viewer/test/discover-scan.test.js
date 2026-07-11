'use strict';

// 親フォルダ登録 → 配下プロジェクトの自動発見のテスト。
// 追加依存なしで `node test/discover-scan.test.js` で走る。
//   - kiro.scanForProjects: kiro-project.yaml（ルート直下 / .kiro/）とマーカーの両方で発見する
//   - kiro.discover: 非プロジェクトの登録ルートを親フォルダとして展開する／
//                    プロジェクトそのものの登録は従来どおり 1 件のまま

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const kiro = require('../src/main/kiro');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-scan-'));
}

function mkdirp(...parts) {
  const dir = path.join(...parts);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

(async () => {
  await test('scanForProjects はルート直下の kiro-project.yaml を発見する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    fs.writeFileSync(path.join(a, 'kiro-project.yaml'), 'root: .\n', 'utf8');
    mkdirp(root, 'not-a-project');
    assert.deepStrictEqual(kiro.scanForProjects(root, 2), [a]);
  });

  await test('scanForProjects は .kiro/kiro-project.yaml とマーカー（charter.md 等）も発見する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    mkdirp(a, '.kiro');
    fs.writeFileSync(path.join(a, '.kiro', 'kiro-project.yaml'), 'root: .\n', 'utf8');
    const b = mkdirp(root, 'beta');
    fs.writeFileSync(path.join(b, 'charter.md'), '# Charter: b\n', 'utf8');
    assert.deepStrictEqual(kiro.scanForProjects(root, 2), [a, b].sort());
  });

  await test('scanForProjects は深さ上限を守り、プロジェクト配下は掘らない', async () => {
    const root = mkRoot();
    const nested = mkdirp(root, 'grp', 'alpha'); // 深さ 2
    fs.writeFileSync(path.join(nested, 'kiro-project.yaml'), 'root: .\n', 'utf8');
    // プロジェクト内部にさらにマーカーがあっても別プロジェクトとして数えない
    const inner = mkdirp(nested, 'sub');
    fs.writeFileSync(path.join(inner, 'charter.md'), '# Charter: inner\n', 'utf8');
    const deep = mkdirp(root, 'g1', 'g2', 'gamma'); // 深さ 3 → 既定 2 では見えない
    fs.writeFileSync(path.join(deep, 'kiro-project.yaml'), 'root: .\n', 'utf8');
    assert.deepStrictEqual(kiro.scanForProjects(root, 2), [nested]);
    assert.deepStrictEqual(kiro.scanForProjects(root, 3), [nested, deep].sort());
  });

  await test('discover は親フォルダ登録を配下プロジェクトへ展開する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    fs.writeFileSync(path.join(a, 'kiro-project.yaml'), 'root: .\n', 'utf8');
    const b = mkdirp(root, 'beta');
    mkdirp(b, 'backlog');
    const cfg = { kiro: { roots: [root], autoDiscover: false } };
    const { projects } = kiro.discover(cfg);
    assert.deepStrictEqual(projects.map((p) => p.dir).sort(), [a, b].sort());
    assert.ok(projects.every((p) => p.source === 'scan'));
  });

  await test('discover はプロジェクトそのものの登録を従来どおり 1 件で扱う', async () => {
    const root = mkRoot();
    fs.writeFileSync(path.join(root, 'kiro-project.yaml'), 'root: .\n', 'utf8');
    mkdirp(root, 'backlog');
    const cfg = { kiro: { roots: [root], autoDiscover: false } };
    const { projects } = kiro.discover(cfg);
    assert.strictEqual(projects.length, 1);
    assert.strictEqual(projects[0].dir, root);
    assert.strictEqual(projects[0].source, 'config');
    assert.strictEqual(projects[0].isProject, true);
  });

  await test('discover は空の親フォルダを従来どおり非プロジェクトの 1 件として残す', async () => {
    const root = mkRoot();
    const cfg = { kiro: { roots: [root], autoDiscover: false } };
    const { projects } = kiro.discover(cfg);
    assert.strictEqual(projects.length, 1);
    assert.strictEqual(projects[0].isProject, false);
  });

  console.log(`\n${passed} tests passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
