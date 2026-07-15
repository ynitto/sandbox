'use strict';

// WSL 内の本体（agent-project）× Windows 側のビュアー（agent-dashboard）の混在で、
// 状態 worktree への正規化が壊れないことを固定する。追加依存なしで
// `node test/state-worktree-wsl.test.js`。
//
// 背景（実際に起きた不具合）: toStateWorktree は git rev-parse の **絶対パス**（--show-toplevel）を
// win32 の path.* で加工していた。git は常にフォワードスラッシュを返し、WSL 内の本体だと
// Linux パス（/home/me/webapp）を返す。これを Windows 側の path で加工すると
//   ・\home\me\webapp-agent-state … ドライブ相対（C:\home\...）に化けて実在せず
//   ・\\wsl.localhost\...\webapp-agent-state\home\me\... … 規約混在で二重連結
// になり、isProjectDir(candidate) が false → 静かに本体（bus の無い main バックアップ）へ
// フォールバック。結果、実行中 run の進捗がビュアーに出なかった。
//
// 修正: git からは --show-prefix（区切り非依存の相対パス）で「深さ」だけを取り、worktree の
// 兄弟パスは root 自身の表記から純関数で組み立てる。ここでは その純関数を規約別に検証する
// （UNC / ドライブ / POSIX を、実 fs・実 git 無しで確認できる）。

const assert = require('assert');
const project = require('../src/main/project');

const { _stateWorktreePath, _sourceRootPath } = project;
const B = 'agent-state';

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('UNC（\\\\wsl.localhost\\...）: 兄弟 worktree を UNC のまま指す（Windows fs で読める）', () => {
  const root = '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project';
  assert.strictEqual(
    _stateWorktreePath(root, '.agent-project/', B),
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project'
  );
});

test('UNC でリポジトリ直下を登録（prefix 空）', () => {
  const root = '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp';
  assert.strictEqual(
    _stateWorktreePath(root, '', B),
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state'
  );
});

test('POSIX（/home/...）: スラッシュ表記を保ったまま兄弟 worktree', () => {
  assert.strictEqual(
    _stateWorktreePath('/home/me/webapp/.agent-project', '.agent-project/', B),
    '/home/me/webapp-agent-state/.agent-project'
  );
});

test('Windows ドライブ（C:\\...）', () => {
  assert.strictEqual(
    _stateWorktreePath('C:\\clones\\webapp\\.agent-project', '.agent-project/', B),
    'C:\\clones\\webapp-agent-state\\.agent-project'
  );
});

test('repo トップより深い相対（prefix 複数段）でも root の相対分を保つ', () => {
  assert.strictEqual(
    _stateWorktreePath('/home/me/webapp/a/b', 'a/b/', B),
    '/home/me/webapp-agent-state/a/b'
  );
});

test('旧実装の破綻（\\home\\... ドライブ相対や二重連結）を生まない', () => {
  const got = _stateWorktreePath('\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project', '.agent-project/', B);
  assert.ok(!/^\\home\\/.test(got), 'ドライブ相対 \\home\\... にならない');
  assert.ok(!/webapp-agent-state\\home\\me/.test(got), 'パスが二重連結されない');
});

test('既に状態 worktree を指しているときは null（呼び出し側が root をそのまま使う）', () => {
  const root = '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project';
  assert.strictEqual(_stateWorktreePath(root, '.agent-project/', B), null);
});

test('逆変換 _sourceRootPath: 状態 worktree → 本体（規約を保つ）', () => {
  assert.strictEqual(
    _sourceRootPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project', '.agent-project/', B),
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project'
  );
  assert.strictEqual(
    _sourceRootPath('/home/me/webapp-agent-state/.agent-project', '.agent-project/', B),
    '/home/me/webapp/.agent-project'
  );
});

test('逆変換: 状態 worktree でなければ null（本体をそのまま使う）', () => {
  assert.strictEqual(
    _sourceRootPath('/home/me/webapp/.agent-project', '.agent-project/', B),
    null
  );
});

console.log(`\n${passed} passed`);
