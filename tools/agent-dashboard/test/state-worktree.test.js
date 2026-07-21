'use strict';

// viewer は「状態の実体」を開く。本体（<repo>/.agent-project）ではなく状態 worktree
// （<repo>-agent-state/.agent-project）を見る。追加依存なしで `node test/state-worktree.test.js`。
//
// 背景: agent-project は root の読み書きを状態 worktree へ逃がす。本体側に残る .agent-project は
// main に載る **バックアップ** であって実体ではない（significant だけが載り、bus＝run の進捗は
// 載らない）。本体を開くと 3 つ壊れる:
//   ・読み  … 古いバックアップを見る。実行中の run が一切見えない（bus が無い）
//   ・書き  … 指示・タスク編集が本体へ落ち、人の作業ツリーを汚す
//   ・git   … gitAutoPush が main へ commit/push する（main はバックアップ専用にしたい）
// さらに本体と worktree が両方登録されると、同じ run が二重に並ぶ。

const assert = require('assert');
const cp = require('child_process');
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

const git = (cwd, ...args) =>
  cp.spawnSync('git', ['-C', cwd, ...args], {
    encoding: 'utf8',
    env: {
      ...process.env,
      GIT_CONFIG_COUNT: '1',
      GIT_CONFIG_KEY_0: 'commit.gpgsign',
      GIT_CONFIG_VALUE_0: 'false',
    },
  });

// <repo> と、その隣に <repo>-agent-state worktree を作る（agent-project がやるのと同じ形）
function scaffold({ withWorktree = true } = {}) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-swt-')));
  const repo = path.join(base, 'repo');
  fs.mkdirSync(repo, { recursive: true });
  git(repo, 'init', '-b', 'main', '.');
  git(repo, 'config', 'user.email', 't@e.com');
  git(repo, 'config', 'user.name', 't');
  fs.writeFileSync(path.join(repo, 'README.md'), 'x\n');
  git(repo, 'add', '-A');
  git(repo, 'commit', '-m', 'init');

  // 本体側の .agent-project（＝main に載るバックアップ。bus は無い）
  const mainSide = path.join(repo, '.agent-project');
  fs.mkdirSync(path.join(mainSide, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(mainSide, 'backlog', 'OLD.md'), '## OLD: 古いバックアップ\n');

  const wt = path.join(base, 'repo-agent-state');
  if (withWorktree) {
    git(repo, 'worktree', 'add', '-b', 'agent-state', wt);
    // 実体側（bus を持つ＝実行中の run が見える）
    const real = path.join(wt, '.agent-project');
    fs.mkdirSync(path.join(real, 'backlog'), { recursive: true });
    fs.mkdirSync(path.join(real, 'bus', 'runs'), { recursive: true });
    fs.writeFileSync(path.join(real, 'backlog', 'LIVE.md'), '## LIVE: 実体\n');
  }
  return { base, repo, mainSide, wt };
}

test('本体の .agent-project を指しても、状態 worktree（実体）へ正規化される', () => {
  const { mainSide, wt } = scaffold();
  const resolved = project.resolveProjectRoot(mainSide);
  assert.strictEqual(
    path.resolve(resolved),
    path.resolve(path.join(wt, '.agent-project')),
    'viewer は実体（worktree）を開く'
  );
  // 実体側にしかない bus が見える＝run が見える
  assert.ok(fs.existsSync(path.join(resolved, 'bus')), 'bus（run の進捗）が見える');
  assert.ok(fs.existsSync(path.join(resolved, 'backlog', 'LIVE.md')), '実体の backlog を読む');
});

test('状態 worktree が無ければ本体のまま（agent-project 未起動・非 git でも壊れない）', () => {
  const { mainSide } = scaffold({ withWorktree: false });
  assert.strictEqual(
    path.resolve(project.resolveProjectRoot(mainSide)),
    path.resolve(mainSide),
    '従来動作にフォールバックする（agent-state ブランチも無いので作らない）'
  );
});

test('状態 worktree が無くても agent-state ブランチがあれば worktree を自動作成して実体を開く', () => {
  const { base, repo, mainSide } = scaffold({ withWorktree: false });
  // .agent-project を載せた agent-state ブランチだけ用意（worktree はまだ無い）。
  git(repo, 'add', '-A');
  git(repo, 'commit', '-m', 'state');
  git(repo, 'branch', 'agent-state');
  const wt = path.join(base, 'repo-agent-state');
  assert.ok(!fs.existsSync(wt), '事前に worktree は無い');

  const resolved = project.resolveProjectRoot(mainSide);

  assert.ok(fs.existsSync(path.join(wt, '.git')), 'worktree を git worktree add で自動作成する');
  assert.strictEqual(
    path.resolve(resolved),
    path.resolve(path.join(wt, '.agent-project')),
    '作成後は実体（worktree）を開く'
  );
  assert.ok(fs.existsSync(path.join(wt, '.agent-project', 'backlog', 'OLD.md')), 'agent-state の状態を取り出す');
});

test('ensureStateWorktree は既存・非 git・ブランチ未存在を no-op にする', () => {
  const withWt = scaffold();                       // 既に worktree あり
  const r1 = project.ensureStateWorktree(path.join(withWt.wt, '.agent-project'));
  assert.strictEqual(r1.created, false, '状態 worktree の中では作らない');
  const r2 = project.ensureStateWorktree(withWt.mainSide);
  assert.ok(r2.created === false && r2.reason === 'exists', '既にあれば作らない');

  const noGit = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-swt-nogit-'));
  assert.strictEqual(project.ensureStateWorktree(noGit).reason, 'non-git', '非 git は no-op');

  const noBranch = scaffold({ withWorktree: false });   // agent-state ブランチ無し
  assert.strictEqual(project.ensureStateWorktree(noBranch.mainSide).reason, 'no-branch', 'ブランチが無ければ作らない');
});

test('既に状態 worktree を指しているときは二重に逃がさない', () => {
  const { wt } = scaffold();
  const real = path.join(wt, '.agent-project');
  assert.strictEqual(
    path.resolve(project.resolveProjectRoot(real)),
    path.resolve(real),
    '<repo>-agent-state-agent-state のような二重リダイレクトを作らない'
  );
});

test('git 管理外のフォルダはそのまま（従来どおり）', () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-nogit-'));
  fs.mkdirSync(path.join(d, 'backlog'), { recursive: true });
  assert.strictEqual(path.resolve(project.resolveProjectRoot(d)), path.resolve(d));
});

test('本体と worktree の両方が登録されても、実体で 1 件に畳まれる', () => {
  const { mainSide, wt } = scaffold();
  const real = path.join(wt, '.agent-project');
  const projects = project.discover({
    projects: { roots: [mainSide, real], autoDiscover: false },
  }).projects || [];
  const roots = projects.map((p) => path.resolve(p.root));
  assert.strictEqual(projects.length, 1, `同じ run が二重に並ばない（実際: ${roots.join(', ')}）`);
  assert.strictEqual(roots[0], path.resolve(real), '状態の置き場は実体を指す');
});

test('readProject の操作基準（dir）が実体を指す＝書き込みが main 側へ落ちない', () => {
  // renderer は readProject の dir を runAction / pinResumeRun / charter 編集に渡す。
  // ここが本体を指すと、指示もタスク編集も main 側の .agent-project へ書かれてしまう。
  const { mainSide, wt } = scaffold();
  const p = project.readProject(mainSide, { projects: {} });
  assert.strictEqual(
    path.resolve(p.dir),
    path.resolve(path.join(wt, '.agent-project')),
    '書き込み先は状態 worktree'
  );
  assert.strictEqual(
    path.resolve(p.busDir),
    path.resolve(path.join(wt, '.agent-project', 'bus')),
    'bus も実体側（run が見える）'
  );
});

console.log(`\n${passed} passed`);
