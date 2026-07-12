'use strict';

// commitPush が本体リポジトリの index / 作業ツリー / ブランチを汚さないことを検証する。
// 追加依存なしで `node test/git-worktree-isolation.test.js` で走る。
//
// 背景: プロジェクトルート（.kiro-project）は成果物リポジトリの中にあり、独自の .git を
// 持たない。そのため rev-parse --show-toplevel は本体リポジトリを指す。以前の commitPush は
// その本体に対して `git add -A -- <dir>` していたため、viewer の操作 1 回で bus/ の実行記録が
// 数百ファイル、人が作業中のステージングへ流れ込んだ（実際に 262 ファイルが staged になった）。
// worker が一時クローンで作業して本体を汚さないのと同じ隔離を、viewer の git 操作にも与える。

const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const git = require('../src/main/git');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const G = (cwd, ...args) =>
  execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    env: { ...process.env, GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'commit.gpgsign', GIT_CONFIG_VALUE_0: 'false' },
  }).trim();

// bare リモート + クローン。クローン内に .kiro-project（状態 + bus の実行記録）を作る。
function scaffold() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-git-'));
  const bare = path.join(root, 'remote.git');
  const work = path.join(root, 'work');
  G(root, 'init', '--bare', '-b', 'main', bare);
  G(root, 'clone', bare, work);
  G(work, 'config', 'user.email', 'test@example.com');
  G(work, 'config', 'user.name', 'test');

  fs.writeFileSync(path.join(work, 'README.md'), '# app\n');
  const proj = path.join(work, '.kiro-project');
  fs.mkdirSync(path.join(proj, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(proj, 'backlog', 'T-1.md'), '## T-1\n- status: ready\n');
  G(work, 'add', '-A');
  G(work, 'commit', '-m', 'init');
  G(work, 'push', '-u', 'origin', 'main'); // 追跡ブランチを張る（commitPush の push 先）
  return { root, bare, work, proj };
}

// kiro-flow が run のたびに書く実行記録を模す
function writeBusRecords(proj, n) {
  const runDir = path.join(proj, 'bus', 'runs', 'run-1', 'results');
  fs.mkdirSync(runDir, { recursive: true });
  for (let i = 0; i < n; i++) {
    fs.writeFileSync(path.join(runDir, `t${i}.json`), JSON.stringify({ id: `t${i}`, output: 'x' }));
  }
}

(async () => {
  await test('本体の index を汚さない（人のステージングを乗っ取らない）', async () => {
    const { work, proj } = scaffold();
    writeBusRecords(proj, 30);
    fs.writeFileSync(path.join(proj, 'backlog', 'T-2.md'), '## T-2\n- status: ready\n');

    // 人が別の作業を staged にしている最中に viewer が同期する
    fs.writeFileSync(path.join(work, 'app.js'), 'console.log(1)\n');
    G(work, 'add', 'app.js');

    const res = await git.commitPush(proj, { message: 'viewer: 操作を反映' });
    assert.strictEqual(res.pushed, true);

    // 人の staged は app.js だけのまま。bus も backlog も本体 index には入っていない
    const staged = G(work, 'diff', '--cached', '--name-only').split('\n').filter(Boolean);
    assert.deepStrictEqual(staged, ['app.js']);
  });

  await test('本体の作業ツリーと HEAD を動かさない', async () => {
    const { work, proj } = scaffold();
    const headBefore = G(work, 'rev-parse', 'HEAD');
    fs.writeFileSync(path.join(proj, 'backlog', 'T-2.md'), '## T-2\n');
    // 人の未コミット変更（本体で rebase/reset されると壊れる）
    fs.writeFileSync(path.join(work, 'README.md'), '# app (編集中)\n');

    await git.commitPush(proj, { message: 'viewer: 操作を反映' });

    assert.strictEqual(G(work, 'rev-parse', 'HEAD'), headBefore); // ローカル HEAD は据え置き
    assert.strictEqual(fs.readFileSync(path.join(work, 'README.md'), 'utf8'), '# app (編集中)\n');
  });

  await test('実行記録（bus）はコミットされず、状態だけが push される', async () => {
    const { work, proj, bare } = scaffold();
    writeBusRecords(proj, 50);
    fs.writeFileSync(path.join(proj, 'backlog', 'T-2.md'), '## T-2\n');

    const res = await git.commitPush(proj, { message: 'viewer: T-2 を投入' });
    assert.strictEqual(res.pushed, true);

    // リモートの main に入った中身を検分する
    const files = G(bare, 'ls-tree', '-r', '--name-only', 'main').split('\n');
    assert.ok(files.includes('.kiro-project/backlog/T-2.md'), '状態は push される');
    assert.ok(!files.some((f) => f.startsWith('.kiro-project/bus/')), 'bus は 1 件も入らない');
  });

  await test('削除も同期される（worktree 側に古いファイルを残さない）', async () => {
    const { work, proj, bare } = scaffold();
    fs.rmSync(path.join(proj, 'backlog', 'T-1.md'));
    const res = await git.commitPush(proj, { message: 'viewer: T-1 を削除' });
    assert.strictEqual(res.pushed, true);
    const files = G(bare, 'ls-tree', '-r', '--name-only', 'main').split('\n');
    assert.ok(!files.includes('.kiro-project/backlog/T-1.md'), '削除がリモートへ伝わる');
  });

  await test('変更が無ければコミットも push もしない', async () => {
    const { proj } = scaffold();
    writeBusRecords(proj, 10); // 実行記録だけが増えた状態＝同期する意味がない
    const res = await git.commitPush(proj, { message: 'viewer: 操作を反映' });
    assert.strictEqual(res.committed, false);
    assert.strictEqual(res.pushed, false);
  });

  await test('worktree を後始末する（残骸を積まない）', async () => {
    const { work, proj } = scaffold();
    fs.writeFileSync(path.join(proj, 'backlog', 'T-2.md'), '## T-2\n');
    await git.commitPush(proj, { message: 'viewer: 操作を反映' });
    const list = G(work, 'worktree', 'list').split('\n').filter(Boolean);
    assert.strictEqual(list.length, 1, '本体の worktree だけが残る');
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
