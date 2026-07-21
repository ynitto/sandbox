'use strict';

// バックログ再分解（エラー回復）のビュアー層テスト。追加依存なしで
// `node test/replan.test.js` で走る。
//   - actions.requestReplan: commands ドロップ一本（案2後半で CLI/フォールバック経路を撤去）
//   - project.replanRequestPending: commands ドロップ・.replan.request マーカーの検知

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const project = require('../src/main/project');

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

function addVersion(dir, name) {
  fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charters', `${name}.md`), `# Charter: ${name}\n## goal\n${name}\n`, 'utf8');
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
      const res = await actions.requestReplan({ projects: { actionMode: 'file' } }, {
        dir,
        reason: '取りこぼし回復',
      });
      assert.strictEqual(res.via, 'file', 'file 経路');
      const { file, rec } = readDropped(dir);
      assert.match(file, /^viewer-replan-project-\d+\.json$/, 'ファイル名は replan-project');
      assert.strictEqual(rec.command, 'replan');
      assert.ok(!('id' in rec), 'プロジェクト単位なので id は載せない');
      assert.strictEqual(rec.reason, '取りこぼし回復');
      assert.strictEqual(rec.actor, 'agent-dashboard');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('タスク追加は選択した計画バージョンを inbox 契約へ保存する', async () => {
    const { root, dir } = mkProject();
    try {
      addVersion(dir, 'v2');
      const res = actions.enqueueToInbox(dir, { title: '版固有の作業', charter: 'v2' });
      const spec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
      assert.strictEqual(spec.charter, 'v2');
      assert.throws(
        () => actions.enqueueToInbox(dir, { title: '不明な版', charter: 'missing' }),
        /計画バージョン.*見つかりません/
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('版指定なしのタスク追加は従来どおり charter キーを省略する', async () => {
    const { root, dir } = mkProject();
    try {
      const res = actions.enqueueToInbox(dir, { title: '従来の作業' });
      const spec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
      assert.ok(!('charter' in spec));
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('タスク追加はルーティング・検収フィールド（workspace/refs/paths/review/expect/followup）を通す', async () => {
    const { root, dir } = mkProject();
    try {
      const res = actions.enqueueToInbox(dir, {
        title: 'モノレポの api を直す',
        workspace: 'app-api',
        paths: ['apps/api/**', 'libs/shared/**'], // 配列はカンマ区切りへ畳む（inbox JSON は両形式可）
        refs: 'design-docs',
        review: 'human',
        expect: 'changes',
        followup: '後続 :: true',
        routed_by: 'owns:apps/api/**', // system が書き戻す監査キー（人の再投入では引き継がない）
      });
      const spec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
      assert.strictEqual(spec.workspace, 'app-api');
      assert.strictEqual(spec.paths, 'apps/api/**, libs/shared/**');
      assert.strictEqual(spec.refs, 'design-docs');
      assert.strictEqual(spec.review, 'human');
      assert.strictEqual(spec.expect, 'changes');
      assert.strictEqual(spec.followup, '後続 :: true');
      assert.ok(!('routed_by' in spec), 'system 管理キーは通さない');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('計画の作り直しは選択したバージョンを replan コマンドへ保存する', async () => {
    const { root, dir } = mkProject();
    try {
      addVersion(dir, 'v2');
      await actions.requestReplan({ projects: { actionMode: 'file' } }, {
        dir,
        reason: 'v2 の計画を回復',
        charter: 'v2',
      });
      const { rec } = readDropped(dir);
      assert.strictEqual(rec.charter, 'v2');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('存在しないバージョンの再計画は要求ファイルを作らず拒否する', async () => {
    const { root, dir } = mkProject();
    try {
      await assert.rejects(
        actions.requestReplan({ projects: { actionMode: 'file' } }, { dir, charter: 'missing' }),
        /計画バージョン.*見つかりません/
      );
      assert.ok(!fs.existsSync(path.join(dir, 'commands')));
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestReplan は空 reason に既定文言を補う', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.requestReplan({ projects: { actionMode: 'file' } }, { dir, reason: '' });
      const { rec } = readDropped(dir);
      assert.ok(rec.reason && rec.reason.length > 0, '既定の理由が入る');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestReplan は稼働状態に依らず commands にドロップする（CLI 経路なし）', async () => {
    // 案2後半: 稼働中/停止中/CLI 有無で分岐せず、常に commands ドロップ一本。
    // CLI 実行・file-fallback・cliError は撤去（「押しても何も起きない」原因不明の停滞の元）。
    const { root, dir } = mkProject();
    const orig = project.isProjectRunning;
    project.isProjectRunning = () => false; // 停止中でも CLI へ行かずドロップ
    try {
      const res = await actions.requestReplan(
        { projects: { command: 'definitely-not-a-real-kiro-binary-xyz' } },
        { dir }
      );
      assert.strictEqual(res.via, 'file', '常に file 経路');
      assert.ok(!('cliError' in res), 'CLI エラー概念は無い');
      const { rec } = readDropped(dir);
      assert.strictEqual(rec.command, 'replan');
    } finally {
      project.isProjectRunning = orig;
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('replanRequestPending は commands ドロップを検知する', async () => {
    const { root, dir } = mkProject();
    try {
      assert.strictEqual(project.replanRequestPending(dir), false, '初期は pending でない');
      await actions.requestReplan({ projects: { actionMode: 'file' } }, { dir });
      assert.strictEqual(project.replanRequestPending(dir), true, 'ドロップ後は pending');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('replanRequestPending は .replan.request マーカーを検知する', async () => {
    const { root, dir } = mkProject();
    try {
      fs.writeFileSync(path.join(dir, '.replan.request'), '{"reason":"x"}', 'utf8');
      assert.strictEqual(project.replanRequestPending(dir), true, 'マーカーがあれば pending');
      fs.rmSync(path.join(dir, '.replan.request'));
      assert.strictEqual(project.replanRequestPending(dir), false, '消えたら pending 解除');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('readProject は replanPending を返す', async () => {
    const { root, dir } = mkProject();
    try {
      const before = project.readProject(dir, {});
      assert.strictEqual(before.replanPending, false);
      fs.writeFileSync(path.join(dir, '.replan.request'), '{"reason":"x"}', 'utf8');
      const after = project.readProject(dir, {});
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
