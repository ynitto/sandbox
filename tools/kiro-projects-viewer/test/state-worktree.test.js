'use strict';

// viewer は「状態の実体」を開く。本体（<repo>/.kiro-project）ではなく状態 worktree
// （<repo>-kiro-state/.kiro-project）を見る。追加依存なしで `node test/state-worktree.test.js`。
//
// 背景: kiro-project は root の読み書きを状態 worktree へ逃がす。本体側に残る .kiro-project は
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

const kiro = require('../src/main/kiro');

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

// <repo> と、その隣に <repo>-kiro-state worktree を作る（kiro-project がやるのと同じ形）
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

  // 本体側の .kiro-project（＝main に載るバックアップ。bus は無い）
  const mainSide = path.join(repo, '.kiro-project');
  fs.mkdirSync(path.join(mainSide, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(mainSide, 'backlog', 'OLD.md'), '## OLD: 古いバックアップ\n');

  const wt = path.join(base, 'repo-kiro-state');
  if (withWorktree) {
    git(repo, 'worktree', 'add', '-b', 'kiro-state', wt);
    // 実体側（bus を持つ＝実行中の run が見える）
    const real = path.join(wt, '.kiro-project');
    fs.mkdirSync(path.join(real, 'backlog'), { recursive: true });
    fs.mkdirSync(path.join(real, 'bus', 'runs'), { recursive: true });
    fs.writeFileSync(path.join(real, 'backlog', 'LIVE.md'), '## LIVE: 実体\n');
  }
  return { base, repo, mainSide, wt };
}

test('本体の .kiro-project を指しても、状態 worktree（実体）へ正規化される', () => {
  const { mainSide, wt } = scaffold();
  const resolved = kiro.resolveProjectRoot(mainSide);
  assert.strictEqual(
    path.resolve(resolved),
    path.resolve(path.join(wt, '.kiro-project')),
    'viewer は実体（worktree）を開く'
  );
  // 実体側にしかない bus が見える＝run が見える
  assert.ok(fs.existsSync(path.join(resolved, 'bus')), 'bus（run の進捗）が見える');
  assert.ok(fs.existsSync(path.join(resolved, 'backlog', 'LIVE.md')), '実体の backlog を読む');
});

test('状態 worktree が無ければ本体のまま（kiro-project 未起動・非 git でも壊れない）', () => {
  const { mainSide } = scaffold({ withWorktree: false });
  assert.strictEqual(
    path.resolve(kiro.resolveProjectRoot(mainSide)),
    path.resolve(mainSide),
    '従来動作にフォールバックする'
  );
});

test('既に状態 worktree を指しているときは二重に逃がさない', () => {
  const { wt } = scaffold();
  const real = path.join(wt, '.kiro-project');
  assert.strictEqual(
    path.resolve(kiro.resolveProjectRoot(real)),
    path.resolve(real),
    '<repo>-kiro-state-kiro-state のような二重リダイレクトを作らない'
  );
});

test('git 管理外のフォルダはそのまま（従来どおり）', () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-nogit-'));
  fs.mkdirSync(path.join(d, 'backlog'), { recursive: true });
  assert.strictEqual(path.resolve(kiro.resolveProjectRoot(d)), path.resolve(d));
});

test('本体と worktree の両方が登録されても、実体で 1 件に畳まれる', () => {
  const { mainSide, wt } = scaffold();
  const real = path.join(wt, '.kiro-project');
  const projects = kiro.discover({
    kiro: { roots: [mainSide, real], autoDiscover: false },
  }).projects || [];
  const roots = projects.map((p) => path.resolve(p.root));
  assert.strictEqual(projects.length, 1, `同じ run が二重に並ばない（実際: ${roots.join(', ')}）`);
  assert.strictEqual(roots[0], path.resolve(real), '状態の置き場は実体を指す');
});

test('readProject の操作基準（dir）が実体を指す＝書き込みが main 側へ落ちない', () => {
  // renderer は readProject の dir を runAction / pinResumeRun / charter 編集に渡す。
  // ここが本体を指すと、指示もタスク編集も main 側の .kiro-project へ書かれてしまう。
  const { mainSide, wt } = scaffold();
  const p = kiro.readProject(mainSide, { kiro: {} });
  assert.strictEqual(
    path.resolve(p.dir),
    path.resolve(path.join(wt, '.kiro-project')),
    '書き込み先は状態 worktree'
  );
  assert.strictEqual(
    path.resolve(p.busDir),
    path.resolve(path.join(wt, '.kiro-project', 'bus')),
    'bus も実体側（run が見える）'
  );
});

console.log(`\n${passed} passed`);
