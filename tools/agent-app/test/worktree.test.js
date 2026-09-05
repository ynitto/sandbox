'use strict';

// 作業フォルダ（git worktree）。名前の検査・一覧の読み方は常に、
// 実物の git を使う統合テストは git がある環境でだけ走る。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync, spawnSync } = require('child_process');

const worktree = require('../src/main/worktree');
const git = require('../src/main/git');
const host = require('../src/main/host');

test('名前の検査: パス区切りと .. を持ち込めない', () => {
  assert.strictEqual(worktree.checkName('feature-x'), 'feature-x');
  assert.strictEqual(worktree.checkName(' a.b@1+2 '), 'a.b@1+2');
  for (const bad of ['..', '../x', 'a/b', 'a\\b', '.hidden', '', '-x', 'x'.repeat(61)]) {
    assert.throws(() => worktree.checkName(bad), /不正/, `拒めていない: ${bad}`);
  }
});

test('ブランチ名 → フォルダ名', () => {
  assert.strictEqual(worktree.slug('feature/foo'), 'feature-foo');
  assert.strictEqual(worktree.slug('fix/#123 の対応'), 'fix-123');
  assert.strictEqual(worktree.slug('  bugfix  '), 'bugfix');
  assert.strictEqual(worktree.slug('///'), '');
});

test('作業フォルダのパスは「登録したパス + .worktrees + 名前」で組む', () => {
  const main = worktree.dirsFor('/repo', '');
  assert.strictEqual(main.fsDir, '/repo');
  assert.strictEqual(main.name, '');
  const wt = worktree.dirsFor('/repo', 'feature-x');
  assert.strictEqual(wt.fsDir, path.join('/repo', '.worktrees', 'feature-x'));
  assert.strictEqual(wt.hostDir, host.toHostPath(wt.fsDir));
  assert.throws(() => worktree.dirsFor('/repo', '../etc'), /不正/);
  // Windows 表記でも fs 用（そのまま）と git 用（WSL 表記）の両方が出る
  if (process.platform === 'win32') {
    const w = worktree.dirsFor('C:\\work\\repo', 'x');
    assert.strictEqual(w.fsDir, 'C:\\work\\repo\\.worktrees\\x');
    assert.strictEqual(w.hostDir, '/mnt/c/work/repo/.worktrees/x');
  }
});

test('git worktree list --porcelain を読む', () => {
  const out = [
    'worktree /home/me/repo', 'HEAD abc123', 'branch refs/heads/main', '',
    'worktree /home/me/repo/.worktrees/feat', 'HEAD def456', 'branch refs/heads/feature/x', '',
    'worktree /home/me/repo/.worktrees/old', 'HEAD 999', 'detached', 'locked 使用中', 'prunable gitdir が無い', '',
  ].join('\n');
  const items = worktree.parseList(out);
  assert.strictEqual(items.length, 3);
  assert.deepStrictEqual(items[0], { path: '/home/me/repo', head: 'abc123', branch: 'main', detached: false, locked: '', prunable: '' });
  assert.strictEqual(items[1].branch, 'feature/x', 'refs/heads/ は落とす');
  assert.strictEqual(items[2].detached, true);
  assert.strictEqual(items[2].locked, '使用中');
  assert.strictEqual(items[2].prunable, 'gitdir が無い');
  assert.deepStrictEqual(worktree.parseList(''), []);
});

test('まとめて撃つ状態取得の出力を読む', () => {
  const text = '\u001e/a\t3\t0\n\u001e/b\t  0\t2\n';
  const map = worktree.parseStatus(text);
  assert.deepStrictEqual(map.get('/a'), { dirty: 3, ahead: 0 });
  assert.deepStrictEqual(map.get('/b'), { dirty: 0, ahead: 2 }, 'wc -l の前後の空白を落とす');
  assert.ok(worktree.statusScript(['/a'], 'HEADSHA').includes("rev-list --count 'HEADSHA'..HEAD"));
  assert.ok(!worktree.statusScript(['/a'], '').includes('rev-list'), '本体の HEAD が無ければ数えない');
});

const hasGit = spawnSync('git', ['--version']).status === 0;

function makeRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-wt-'));
  const run = (args) => execFileSync('git', ['-C', repo, ...args], { stdio: 'pipe' });
  run(['init', '-q', '-b', 'main']);
  run(['config', 'user.email', 't@example.com']);
  run(['config', 'user.name', 't']);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
  run(['add', '.']);
  run(['commit', '-qm', 'init']);
  return { repo, run };
}

test('統合: 作業フォルダを作り・見て・消す', { skip: !hasGit && 'git が無い' }, async () => {
  const { repo, run } = makeRepo();
  try {
    const first = await worktree.list(repo);
    assert.strictEqual(first.error, '');
    assert.strictEqual(first.items.length, 1);
    assert.ok(first.items[0].main && first.items[0].branch === 'main' && !first.items[0].selectable);

    const made = await worktree.create(repo, { branch: 'feature/x' });
    assert.strictEqual(made.name, 'feature-x');
    assert.strictEqual(made.branch, 'feature/x');
    assert.strictEqual(made.reusedBranch, false);
    const dirs = worktree.dirsFor(repo, 'feature-x');
    assert.ok(fs.existsSync(path.join(dirs.fsDir, 'a.txt')), '本体の中身が生えている');

    // 本体の変更ビューに .worktrees が「新規」として並ばない（.git/info/exclude）
    const excl = fs.readFileSync(path.join(repo, '.git', 'info', 'exclude'), 'utf8');
    assert.ok(excl.includes('/.worktrees/'));
    assert.strictEqual(execFileSync('git', ['-C', repo, 'status', '--porcelain', '-uall'], { encoding: 'utf8' }).trim(), '');
    await worktree.ensureExcluded(repo);
    assert.strictEqual(fs.readFileSync(path.join(repo, '.git', 'info', 'exclude'), 'utf8'), excl, '二重に足さない');

    // 作業フォルダの中だけが変わり、本体は綺麗なまま
    fs.writeFileSync(path.join(dirs.fsDir, 'b.txt'), 'new\n');
    fs.writeFileSync(path.join(dirs.fsDir, 'a.txt'), 'two\n');
    const listed = await worktree.list(repo);
    assert.strictEqual(listed.items.length, 2);
    const wt = listed.items[1];
    assert.deepStrictEqual([wt.name, wt.branch, wt.selectable, wt.dirty, wt.ahead], ['feature-x', 'feature/x', true, 2, 0]);
    assert.strictEqual(listed.items[0].dirty, 0, '本体は綺麗');

    const inWt = await git.changes(dirs.hostDir);
    assert.deepStrictEqual(inWt.files.map((f) => f.file).sort(), ['a.txt', 'b.txt']);
    assert.strictEqual(inWt.branch, 'feature/x');
    assert.strictEqual((await git.changes(host.toHostPath(repo))).files.length, 0);

    // コミットすると「作業ツリー」は綺麗になり、「ブランチ」に出る
    execFileSync('git', ['-C', dirs.fsDir, 'add', '.'], { stdio: 'pipe' });
    execFileSync('git', ['-C', dirs.fsDir, 'commit', '-qm', 'work'], { stdio: 'pipe' });
    assert.strictEqual((await worktree.list(repo)).items[1].ahead, 1);
    assert.strictEqual((await git.changes(dirs.hostDir)).files.length, 0);
    const branchView = await git.changes(dirs.hostDir, '', { scope: 'branch', base: 'main' });
    assert.deepStrictEqual(branchView.files.map((f) => [f.file, f.label]).sort(), [['a.txt', '変更'], ['b.txt', '追加']]);
    assert.ok(branchView.diff.includes('+two') && branchView.diff.includes('+new'));
    assert.ok((await git.fileDiff(dirs.hostDir, 'b.txt', '', { scope: 'branch', base: 'main' })).includes('+new'));
    assert.strictEqual((await git.changes(dirs.hostDir, '', { scope: 'branch', base: 'nope' })).error, '分岐元を決められません（nope）');

    // 既にあるブランチは持ってくるだけ（`-b` を付けない）
    run(['branch', 'ready']);
    const reused = await worktree.create(repo, { branch: 'ready' });
    assert.strictEqual(reused.reusedBranch, true);
    assert.strictEqual(reused.name, 'ready');
    // 他の worktree が使っているブランチは git が断る
    await assert.rejects(worktree.create(repo, { branch: 'ready', name: 'ready2' }), /worktree を作れません/);
    await assert.rejects(worktree.create(repo, { branch: 'x', base: 'nosuchref' }), /分岐元が見つかりません/);
    await assert.rejects(worktree.create(repo, { branch: 'bad..name' }), /ブランチ名として使えません/);

    // 分岐元を指定して作れる
    const fromMain = await worktree.create(repo, { branch: 'from-main', base: 'main' });
    assert.strictEqual(fromMain.branch, 'from-main');

    // 片付け: 未コミットが残っていると断り、force で押し切れる
    fs.writeFileSync(path.join(worktree.dirsFor(repo, 'ready').fsDir, 'dirty.txt'), 'x\n');
    await assert.rejects(worktree.remove(repo, 'ready'), /未コミットの変更/);
    const gone = await worktree.remove(repo, 'ready', { force: true, deleteBranch: true });
    assert.deepStrictEqual([gone.removed, gone.branch, gone.branchRemoved], [true, 'ready', true]);
    assert.ok(!fs.existsSync(worktree.dirsFor(repo, 'ready').fsDir));

    // マージしていないブランチは既定（-d）では消えない——理由を返して worktree の削除自体は済ませる
    const kept = await worktree.remove(repo, 'feature-x', { deleteBranch: true });
    assert.strictEqual(kept.removed, true);
    assert.strictEqual(kept.branchRemoved, false);
    assert.match(kept.branchError, /not fully merged|マージ/i);
    assert.ok(execFileSync('git', ['-C', repo, 'branch', '--list', 'feature/x'], { encoding: 'utf8' }).trim(), 'ブランチは残る');

    await assert.rejects(worktree.remove(repo, 'nosuch'), /作業フォルダが見つかりません/);
    await assert.rejects(worktree.find(repo, 'from-main-nope'), /作業フォルダが見つかりません/);
    assert.strictEqual((await worktree.find(repo, 'from-main')).branch, 'from-main');
  } finally {
    host.closeAll();
  }
});

test('統合: git リポジトリでないフォルダはエラーを返す（画面は本体だけで動く）', { skip: !hasGit && 'git が無い' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-nogit-'));
  const res = await worktree.list(dir);
  assert.ok(res.error);
  assert.deepStrictEqual(res.items, []);
  host.closeAll();
});

test('統合: agent-flow が origin へ公開したブランチを取得して作業フォルダで開く', { skip: !hasGit && 'git が無い' }, async () => {
  const { repo, run } = makeRepo();
  const remote = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-wt-remote-'));
  execFileSync('git', ['init', '--bare', '-q', remote], { stdio: 'pipe' });
  try {
    run(['remote', 'add', 'origin', remote]);
    run(['push', '-q', '-u', 'origin', 'main']);
    run(['checkout', '-qb', 'af/published']);
    fs.writeFileSync(path.join(repo, 'published.txt'), 'agent-flow result\n');
    run(['add', 'published.txt']);
    run(['commit', '-qm', 'publish']);
    run(['push', '-q', 'origin', 'af/published']);
    run(['checkout', '-q', 'main']);
    run(['branch', '-D', 'af/published']);
    run(['update-ref', '-d', 'refs/remotes/origin/af/published']);

    const made = await worktree.create(repo, { branch: 'af/published', fetchRemote: true });
    assert.strictEqual(made.trackedRemote, true);
    assert.ok(fs.existsSync(path.join(worktree.dirsFor(repo, made.name).fsDir, 'published.txt')));
  } finally {
    host.closeAll();
  }
});
