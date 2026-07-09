'use strict';

// バックログ再分解（エラー回復）のビュアー層テスト。追加依存なしで
// `node test/replan.test.js` で走る。
//   - actions.requestReplan: file / cli / fallback の 3 経路と commands ドロップの形
//   - kiro.replanRequestPending: commands ドロップ・.replan.request マーカーの検知

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const kiro = require('../src/main/kiro');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-replan-'));
  const dir = path.join(root, 'projects', 'demo');
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  return { root, dir };
}

function readDropped(dir) {
  const cdir = path.join(dir, 'commands');
  const files = fs.readdirSync(cdir).filter((f) => f.endsWith('.json'));
  assert.strictEqual(files.length, 1, 'commands に 1 件だけドロップされる');
  return { file: files[0], rec: JSON.parse(fs.readFileSync(path.join(cdir, files[0]), 'utf8')) };
}

(async () => {
  await test('requestReplan(file モード) は id 無しの replan コマンドをドロップする', async () => {
    const { root, dir } = mkProject();
    try {
      const res = await actions.requestReplan({ kiro: { actionMode: 'file' } }, {
        dir,
        reason: '取りこぼし回復',
      });
      assert.strictEqual(res.via, 'file', 'file 経路');
      const { file, rec } = readDropped(dir);
      assert.match(file, /^viewer-replan-project-\d+\.json$/, 'ファイル名は replan-project');
      assert.strictEqual(rec.command, 'replan');
      assert.ok(!('id' in rec), 'プロジェクト単位なので id は載せない');
      assert.strictEqual(rec.reason, '取りこぼし回復');
      assert.strictEqual(rec.actor, 'kiro-projects-viewer');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestReplan は空 reason に既定文言を補う', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.requestReplan({ kiro: { actionMode: 'file' } }, { dir, reason: '' });
      const { rec } = readDropped(dir);
      assert.ok(rec.reason && rec.reason.length > 0, '既定の理由が入る');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestReplan(auto・稼働中) は commands にドロップする', async () => {
    const { root, dir } = mkProject();
    const orig = kiro.isProjectRunning;
    kiro.isProjectRunning = () => true; // 稼働中扱い → file-drop 経路
    try {
      const res = await actions.requestReplan({ kiro: { actionMode: 'auto' } }, { dir });
      assert.strictEqual(res.via, 'file');
      const { rec } = readDropped(dir);
      assert.strictEqual(rec.command, 'replan');
    } finally {
      kiro.isProjectRunning = orig;
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestReplan(auto・停止中・CLI 不可) は要求ファイルへ退避する', async () => {
    const { root, dir } = mkProject();
    const orig = kiro.isProjectRunning;
    kiro.isProjectRunning = () => false; // 停止中 → CLI を試み、失敗したらドロップ退避
    try {
      const res = await actions.requestReplan(
        { kiro: { actionMode: 'auto', command: 'definitely-not-a-real-kiro-binary-xyz' } },
        { dir }
      );
      assert.strictEqual(res.via, 'file-fallback', 'CLI 失敗でドロップ退避');
      assert.ok(res.cliError, 'CLI エラーを添える');
      const { rec } = readDropped(dir);
      assert.strictEqual(rec.command, 'replan');
    } finally {
      kiro.isProjectRunning = orig;
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('replanRequestPending は commands ドロップを検知する', async () => {
    const { root, dir } = mkProject();
    try {
      assert.strictEqual(kiro.replanRequestPending(dir), false, '初期は pending でない');
      await actions.requestReplan({ kiro: { actionMode: 'file' } }, { dir });
      assert.strictEqual(kiro.replanRequestPending(dir), true, 'ドロップ後は pending');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('replanRequestPending は .replan.request マーカーを検知する', async () => {
    const { root, dir } = mkProject();
    try {
      fs.writeFileSync(path.join(dir, '.replan.request'), '{"reason":"x"}', 'utf8');
      assert.strictEqual(kiro.replanRequestPending(dir), true, 'マーカーがあれば pending');
      fs.rmSync(path.join(dir, '.replan.request'));
      assert.strictEqual(kiro.replanRequestPending(dir), false, '消えたら pending 解除');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('readProject は replanPending を返す', async () => {
    const { root, dir } = mkProject();
    try {
      const before = kiro.readProject(dir, {});
      assert.strictEqual(before.replanPending, false);
      fs.writeFileSync(path.join(dir, '.replan.request'), '{"reason":"x"}', 'utf8');
      const after = kiro.readProject(dir, {});
      assert.strictEqual(after.replanPending, true);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
