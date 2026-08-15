'use strict';

// 常駐一本化（設計 §4.6・実装計画 P2）の非退行テスト。
// dashboard は状態を共有するリポジトリへ **書かない**・本体を起こさない・ロックを覗かない。
// この 3 つはコードを足せば簡単に戻ってしまう（実際、過去に viewer の push が本体の
// 状態ファイルへコンフリクトマーカーを書き込んで状態を失わせた）ので、構造として固定する。
// 追加依存なしで `node test/no-git-writes.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const SRC = path.join(__dirname, '..', 'src');

// 検査対象は `src/` 全体。以前は「プロジェクトの状態を扱う層」（base/main・
// features/agent-project・renderer）だけを見ていたが、制御面が 2 つから 7 つへ増えた結果、
// **後から足した feature には護りが掛かっていない**状態になっていた。どの feature も
// 規律自体は守っているものの、それを保証しているのが人の目だけ、という穴を塞ぐ。
//
// 除外は cowork だけ。定常業務は**人の成果物リポジトリ**でブランチを切って push する機能で、
// 常駐体が持つ状態リポジトリには触らない（除外の理由が 1 つに絞られていることが大事で、
// 「範囲外だから」という理由で例外が増えないようにする）。
const EXCLUDED = [path.join(SRC, 'features', 'cowork')];

function sourceFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules') continue;
    const p = path.join(dir, e.name);
    if (EXCLUDED.some((x) => p === x || p.startsWith(`${x}${path.sep}`))) continue;
    if (e.isDirectory()) out.push(...sourceFiles(p));
    else if (e.name.endsWith('.js')) out.push(p);
  }
  return out;
}

const files = sourceFiles(SRC);
const rel = (p) => path.relative(path.join(__dirname, '..'), p);

// コメントは対象外（「なぜ消したか」を書き残すのが目的なので、語そのものは残ってよい）
function codeOf(file) {
  return fs
    .readFileSync(file, 'utf8')
    .split('\n')
    .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
    .join('\n');
}

test('検査範囲が全制御面を覆う（cowork のみ除外）', () => {
  // 範囲そのものを検査する。護りの中身より先に**掛かっている範囲**が縮むほうが起きやすく、
  // しかも気づきにくい（テストは緑のまま、見ていない場所が増える）。新しい feature を
  // 足したら自動的にこの護りの下に入り、外したいなら EXCLUDED を触るしかない形にする。
  const featureDirs = fs
    .readdirSync(path.join(SRC, 'features'), { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name);
  assert.ok(featureDirs.length >= 7, `制御面が見つからない: ${featureDirs}`);
  for (const name of featureDirs) {
    const prefix = path.join(SRC, 'features', name) + path.sep;
    const covered = files.some((f) => f.startsWith(prefix));
    if (name === 'cowork') {
      assert.ok(!covered, 'cowork は成果物リポジトリを扱うので除外されているはず');
    } else {
      assert.ok(covered, `${name} が検査範囲から外れている`);
    }
  }
  assert.ok(files.includes(path.join(SRC, 'preload.js')), 'preload も範囲に入るはず');
});

test('状態リポジトリを書き換える git サブコマンドを起動しない', () => {
  // 読み取り（rev-parse / status / diff / rev-list / merge-base）と、成果物リポジトリの
  // fetch だけが許される。作業ツリー・履歴・リモートを変える口はここで落とす。
  const forbidden = ['pull', 'push', 'commit', 'rebase', 'merge', 'worktree', 'checkout', 'branch', 'add', 'stash'];
  for (const file of files) {
    const code = codeOf(file);
    for (const sub of forbidden) {
      const re = new RegExp(`['"\`]${sub}['"\`]\\s*,`);
      const hit = code.split('\n').find((line) => /git|gitOnce/.test(line) && re.test(line));
      assert.ok(!hit, `${rel(file)}: git ${sub} を起動している: ${hit}`);
    }
  }
});

test('ワークフロー画面は成果物リポジトリの共有フローを書き換えない', () => {
  const adhoc = require('../src/features/adhoc-flow/main/adhoc');
  const src = codeOf(path.join(SRC, 'features', 'adhoc-flow', 'main', 'adhoc.js'));
  // 読み取り専用スコープ（リポジトリ共有・同梱）は保存も削除も受け付けない
  for (const scope of ['repository', 'builtin']) {
    assert.throws(() => adhoc.saveWorkflow({}, { _scope: scope, name: 'x', nodes: [] }),
      /読み取り専用/, `${scope} の保存拒否が必要`);
    assert.throws(() => adhoc.deleteWorkflow({}, 'any', { scope }),
      /読み取り専用/, `${scope} の削除拒否が必要`);
  }
  assert.ok(!/writeJsonAtomic\([^\n]*repositoryWorkflowDir/.test(src),
    'リポジトリ共有ディレクトリへ直接書いてはならない');
  assert.ok(!/writeJsonAtomic\([^\n]*repositoryMethodsDir/.test(src),
    'リポジトリ共有の手法ディレクトリへ直接書いてはならない');
  assert.strictEqual(typeof adhoc.repositoryWorkflowDir, 'function');
  assert.strictEqual(typeof adhoc.repositoryMethodsDir, 'function');
});

test('git の書き込み API を IPC・preload に載せない', () => {
  const gone = ['git:pull', 'git:commitPush', 'git:heal', 'gitCommitPush', 'gitHeal', 'gitPull'];
  for (const file of files) {
    const code = codeOf(file);
    for (const name of gone) {
      assert.ok(!code.includes(name), `${rel(file)}: ${name} は削除済みのはず`);
    }
  }
});

test('設計runは読み取り専用契約をplanへ運び、設計セッション自身はGit操作を持たない', () => {
  const adhoc = require('../src/features/adhoc-flow/main/adhoc');
  const profiles = require('../src/features/orchestration/main/profiles');
  const designSessionSource = codeOf(
    path.join(SRC, 'features', 'adhoc-flow', 'main', 'design-session.js'),
  );
  const workflow = {
    version: 2,
    id: 'design-readonly-guard',
    name: '設計読み取り専用ガード',
    purpose: 'design',
    entry: ['draft'],
    exit: ['finish'],
    nodes: [
      { id: 'draft', goal: '要望を整理する', kind: 'work', tier: 'large', deps: [] },
      { id: 'finish', goal: '設計書を出力する', kind: 'synthesize', tier: 'large', deps: ['draft'] },
    ],
  };
  const originalResolveTier = profiles.resolveTier;
  profiles.resolveTier = () => ({ agent_cli: 'codex', model: 'test' });
  try {
    const plan = adhoc.planFromWorkflow({}, workflow, { purpose: 'design' });
    assert.strictEqual(plan.nodes.length, 2);
    for (const node of plan.nodes) {
      assert.match(node.goal, /リポジトリは読み取り専用です/);
      assert.match(node.goal, /commit、pushは禁止です/);
      assert.doesNotMatch(node.goal, /af\//, '設計成果へ作業ブランチを持ち込まない');
    }
    assert.match(plan.nodes.find((node) => node.id === 'finish').goal, /## 目的/);
    assert.match(plan.nodes.find((node) => node.id === 'finish').goal, /## 検証方法/);

    // 設計セッションは既存の adhoc.submit 契約を使うだけで、Gitの書込み経路を増やさない。
    assert.match(designSessionSource, /adhoc\.submit\(config/);
    assert.doesNotMatch(designSessionSource, /\b(?:git|commit|branch|checkout)\b/);
    assert.ok(!designSessionSource.split('\n').some((line) =>
      /\b(?:commit|push)\b/.test(line) && !/\.push\s*\(/.test(line)));
  } finally {
    profiles.resolveTier = originalResolveTier;
  }
});

test('読み取り専用の git 層は読み取り関数だけを公開する', () => {
  // resolveDiffRoot は「どのディレクトリの差分を見るか」を決めるだけの純関数（S4-e）。
  // ここに増やしてよいのは読み取りだけで、書き込み（pull / commit / push）は戻さないこと。
  const git = require('../src/main/git');
  assert.deepStrictEqual(
    Object.keys(git).sort(),
    ['bridgeRepoPath', 'diagnostics', 'diffRange', 'health', 'resolveDiffRoot']
  );
});

test('本体（agent-project）を CLI で起こす経路を持たない', () => {
  const gone = ['dashboard:start', 'startProject', 'runProjectCli', 'agent-project start'];
  for (const file of files) {
    // AI 補助（読み取り専用のエージェント CLI 起動）は存置する。対象外にするのは
    // agent.js だけで、そこも agent-project 本体は起動しない。
    if (file.endsWith(path.join('main', 'agent.js'))) continue;
    const code = codeOf(file);
    for (const name of gone) {
      assert.ok(!code.includes(name), `${rel(file)}: ${name} は削除済みのはず`);
    }
  }
});

test('実行エンジンのロック鍵を手写ししない（稼働表示は engine/status.json 一本）', () => {
  const gone = ['daemonLockPath', 'daemonStatus', 'stopDaemon', 'flowLockDir', 'agent-flow-locks', 'local::'];
  for (const file of files) {
    const code = codeOf(file);
    for (const name of gone) {
      assert.ok(!code.includes(name), `${rel(file)}: ${name} は削除済みのはず`);
    }
  }
});

test('プロジェクトを列挙する設定を持たない（発見は実行エンジンから）', () => {
  const defaults = require('../src/features/agent-project/config');
  assert.strictEqual(defaults.projects.roots, undefined);
  assert.strictEqual(defaults.projects.scanDepth, undefined);
  assert.strictEqual(defaults.projects.autoDiscover, undefined);
  assert.ok(defaults.engine, '実行エンジンの在り処が設定の入口');
});

test('実行エンジンと共有する置き場をこの PC のホームで解決しない', () => {
  // 正典構成は「Windows の画面 + WSL の実行エンジン」。実行エンジンと読み書きを共有する
  // 置き場（engine/status.json・この端末への指示）を os.homedir() 基準で解決すると、
  // 投函先と取り込み先が別ファイルシステムになり、押しても何も起きない（P0-2 で実際に
  // 踏んだ）。ホームの解決は engine.agentsHome() の 1 実装を通すこと。
  const shared = [
    path.join(SRC, 'features', 'delegation', 'main', 'node-commands.js'),
    // host.yaml（repos[] のローカルクローン宣言）も実行エンジンと共有する置き場。
    // ここを os.homedir() で解決していたため、Windows の画面では WSL 側の宣言が
    // 一度も読まれず、CLIチャットの起動先・検収差分が「宣言が無い」と言い続けた。
    path.join(SRC, 'features', 'agent-project', 'main', 'nodeRepos.js'),
  ];
  for (const file of shared) {
    const code = codeOf(file);
    assert.ok(!/\bagentHomeSubdir\b/.test(code),
      `${rel(file)}: agentHomeSubdir はこの PC のホーム基準（実行エンジンのホームを通すこと）`);
    // os.homedir() は `~` の展開（利用者が明示したパス）にだけ使ってよい
    const homedirLines = code.split('\n').filter((line) => /os\.homedir\(\)/.test(line));
    for (const line of homedirLines) {
      assert.ok(/~/.test(line),
        `${rel(file)}: 既定の置き場を os.homedir() で決めている: ${line.trim()}`);
    }
  }
});

test('利用者に見せる文へ内部名（node / sync / resident / 常駐体）を出さない（R10）', () => {
  // 内部部品の名前は設計上の呼び名で、利用者の語彙ではない。表示文はコマンド名
  // （agent-project serve）と「実行エンジン」「実行する PC」で語る。
  const internal = /\bnode\b|\bsync\b|\bresident\b|常駐体|ノード/i;
  const engine = require('../src/features/agent-project/main/engine');
  const cases = [
    { exists: false },
    { exists: true, running: false, ageSec: 900, contractVersion: 1, syncHealth: [], children: [] },
    { exists: true, running: true, contractVersion: 0, syncHealth: [], children: [] },
    { exists: true, running: true, contractVersion: 1, syncHealth: [{ behind: 2, ahead: 1 }], children: [] },
    { exists: true, running: true, contractVersion: 1, syncHealth: [{ lastError: 'x' }], children: [] },
    { exists: true, running: true, contractVersion: 1, syncHealth: [], children: [{ name: 'a', quarantined: true }] },
    { exists: true, running: true, contractVersion: 1, syncHealth: [], children: [] },
  ];
  for (const c of cases) {
    const { summary } = engine.summarize(c);
    assert.ok(!internal.test(summary), `内部名が出ている: ${summary}`);
  }
  // 設定画面（実行エンジンの場所）も同じ語彙で書く
  const orch = fs.readFileSync(path.join(SRC, 'renderer', 'sections', 'orchestration.js'), 'utf8');
  const pane = orch.slice(orch.indexOf('function globalSettingsSyncHtml'), orch.indexOf('function globalSettingsRoutineHtml'));
  assert.ok(!internal.test(pane.replace(/id="[^"]*"|for="[^"]*"|class="[^"]*"/g, '')), pane);
});

console.log(`\n${passed} passed`);
