'use strict';

// state_repo 設定があるとき、登録した成果物リポジトリから状態専用 clone をルートとして
// 解決する。本体 `_redirect_root_to_state_repo` と同型。追加依存なしで
// `node test/state-repo-root.test.js`。

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

function initRepo(dir, { bare = false, commit = true } = {}) {
  fs.mkdirSync(dir, { recursive: true });
  if (bare) {
    git(dir, 'init', '--bare', '-b', 'main', '.');
  } else {
    git(dir, 'init', '-b', 'main', '.');
    git(dir, 'config', 'user.email', 't@e.com');
    git(dir, 'config', 'user.name', 't');
    if (commit) {
      fs.writeFileSync(path.join(dir, 'README.md'), 'x\n');
      git(dir, 'add', '-A');
      git(dir, 'commit', '-m', 'init');
    }
  }
}

// 成果物 repo + 状態専用 remote/clone + .agents/agent-project.yaml を並べる。
function scaffold({ stateRepoDir = 'app-state', writeYaml = true } = {}) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-srr-')));
  const remote = path.join(base, 'state.git');
  initRepo(remote, { bare: true });

  // bare に最初のコミットを載せる（clone 可能な remote にする）
  const seed = path.join(base, 'seed');
  initRepo(seed);
  fs.mkdirSync(path.join(seed, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(seed, 'charter.md'), '# Charter: state\n## goal\nx\n');
  fs.writeFileSync(path.join(seed, 'backlog', 'T1.md'), '## T1: やること\n- status: ready\n');
  git(seed, 'remote', 'add', 'origin', remote);
  git(seed, 'add', '-A');
  git(seed, 'commit', '-m', 'state');
  git(seed, 'push', '-u', 'origin', 'main');

  const deliverable = path.join(base, 'app');
  initRepo(deliverable);
  if (writeYaml) {
    fs.mkdirSync(path.join(deliverable, '.agents'), { recursive: true });
    const dirLine = stateRepoDir ? `state_repo_dir: ${stateRepoDir}\n` : '';
    fs.writeFileSync(
      path.join(deliverable, '.agents', 'agent-project.yaml'),
      `state_repo: ${remote}\nstate_repo_branch: main\n${dirLine}`,
      'utf8'
    );
  }

  const cloneName = stateRepoDir || 'app-state';
  const clone = path.isAbsolute(cloneName)
    ? cloneName
    : path.join(base, cloneName);
  // エンジン相当: 状態専用リポジトリを通常 clone（branch 明示で空 checkout を避ける）
  const cr = git(base, 'clone', '-q', '--branch', 'main', remote, clone);
  if (cr.status !== 0) {
    throw new Error(`clone failed: ${cr.stderr || cr.stdout}`);
  }
  return { base, remote, deliverable, clone };
}

test('resolveProjectRoot は state_repo_dir の clone をルートにする', () => {
  const { deliverable, clone } = scaffold({ stateRepoDir: 'sandbox-project' });
  try {
    assert.strictEqual(
      path.resolve(project.resolveProjectRoot(deliverable)),
      path.resolve(clone),
      '成果物を登録しても状態 clone を開く'
    );
  } finally {
    fs.rmSync(path.dirname(deliverable), { recursive: true, force: true });
  }
});

test('state_repo_dir 未指定なら既定 <repo>-state を使う', () => {
  const { deliverable, clone, base } = scaffold({ stateRepoDir: '' });
  try {
    assert.strictEqual(path.resolve(clone), path.resolve(path.join(base, 'app-state')));
    assert.strictEqual(
      path.resolve(project.resolveProjectRoot(deliverable)),
      path.resolve(clone)
    );
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('state_repo_dir 絶対パスはそのまま使う', () => {
  const outer = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-srr-abs-')));
  const absClone = path.join(outer, 'elsewhere', 'mystate');
  fs.mkdirSync(path.dirname(absClone), { recursive: true });
  const { deliverable, clone, base } = scaffold({ stateRepoDir: absClone });
  try {
    assert.strictEqual(path.resolve(clone), path.resolve(absClone));
    assert.strictEqual(
      path.resolve(project.resolveProjectRoot(deliverable)),
      path.resolve(absClone)
    );
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
    fs.rmSync(outer, { recursive: true, force: true });
  }
});

test('clone 未作成なら従来の worktree 方式へフォールバック（-agent-state を勝手に作らない経路も維持）', () => {
  const { deliverable, clone, base } = scaffold({ stateRepoDir: 'missing-state' });
  try {
    fs.rmSync(clone, { recursive: true, force: true });
    // 成果物側に状態マーカーが無ければ resolve(ws) → worktree 未作成なら ws のまま
    const resolved = project.resolveProjectRoot(deliverable);
    assert.strictEqual(path.resolve(resolved), path.resolve(deliverable));
    assert.ok(!fs.existsSync(path.join(base, 'app-agent-state')), 'フォールバックで旧 worktree を増やさない');
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('origin が state_repo と食い違うディレクトリは使わない', () => {
  const { deliverable, clone, base, remote } = scaffold({ stateRepoDir: 'wrong-origin' });
  try {
    // clone の origin を別 remote に差し替え（旧 worktree / 別 repo 衝突を模擬）
    const other = path.join(base, 'other.git');
    initRepo(other, { bare: true });
    git(clone, 'remote', 'set-url', 'origin', other);
    assert.strictEqual(
      project.resolveStateRepoRoot(deliverable, {
        state_repo: remote,
        state_repo_dir: 'wrong-origin',
      }),
      null,
      'origin 不一致のディレクトリは状態ルートにしない'
    );
    assert.strictEqual(
      path.resolve(project.resolveProjectRoot(deliverable)),
      path.resolve(deliverable)
    );
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('状態 clone を直接登録した場合もそのままルート（origin 一致）', () => {
  const { clone, remote, base } = scaffold();
  try {
    // 状態 clone 側にもブートストラップ yaml が無いのが本体の設計だが、
    // origin 一致の直登録は yaml 無しでも従来の hasProjectStateMarkers 経路で開く。
    // yaml 付きで state_repo を書いた場合も、自分自身へ解決されること。
    fs.mkdirSync(path.join(clone, '.agents'), { recursive: true });
    fs.writeFileSync(
      path.join(clone, '.agents', 'agent-project.yaml'),
      `state_repo: ${remote}\nstate_repo_branch: main\nstate_repo_dir: ignored-when-self\n`,
      'utf8'
    );
    assert.strictEqual(
      path.resolve(project.resolveProjectRoot(clone)),
      path.resolve(clone)
    );
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('readProject は成果物ワークスペース登録でも状態 clone から backlog を読む', () => {
  const { deliverable, clone, base } = scaffold({ stateRepoDir: 'sandbox-project' });
  try {
    const p = project.readProject(deliverable, { projects: {} });
    assert.strictEqual(p.workspace, path.resolve(deliverable));
    assert.strictEqual(p.dir, path.resolve(clone));
    assert.strictEqual(p.backlog.length, 1);
    assert.strictEqual(p.charter.name, 'state');
    assert.strictEqual(p.busDir, path.join(path.resolve(clone), 'bus'));
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('discover は成果物登録でも root を状態 clone にする', () => {
  const { deliverable, clone, base } = scaffold({ stateRepoDir: 'sandbox-project' });
  try {
    const { projects } = project.discover({
      projects: { roots: [deliverable], autoDiscover: false },
    });
    const p = projects.find((x) => x.dir === path.resolve(deliverable));
    assert.ok(p, '登録した成果物ワークスペースが 1 件');
    assert.strictEqual(p.root, path.resolve(clone));
    assert.strictEqual(p.backlogCount, 1);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('_sameGitRemote は末尾 .git とスラッシュ差を吸収する', () => {
  assert.ok(project._sameGitRemote(
    'https://example.com/a/b.git',
    'https://example.com/a/b/'
  ));
  assert.ok(!project._sameGitRemote(
    'https://example.com/a/b.git',
    'https://example.com/a/c.git'
  ));
});

console.log(`\n${passed} passed`);
