'use strict';

// 同期の健康状態（health）と一発修復（heal）の検証。
// 追加依存なしで `node test/git-heal.test.js` で走る。
//
// 背景: 分散構成（agent-project=WSL / viewer=Windows）で agent-state ブランチが食い違うと、
// 従来は「pull できない・push できない・原因は git 用語でしか分からない」で人が詰んだ。
// health は状態を平易な一文で言い、heal はロック残骸の掃除 → 取り込み → 合流 → 送信を
// 1 ボタンで安全に行う（force push しない・人の未コミット変更は消さない）。

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

// bare リモート + 2 クローン（viewer 側 / もう一方の書き手）
function scaffold() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-heal-'));
  const bare = path.join(root, 'remote.git');
  const work = path.join(root, 'work');
  const other = path.join(root, 'other');
  G(root, 'init', '--bare', '-b', 'main', bare);
  G(root, 'clone', bare, work);
  G(work, 'config', 'user.email', 'v@example.com');
  G(work, 'config', 'user.name', 'viewer');
  fs.writeFileSync(path.join(work, 'journal.md'), 'base\n');
  G(work, 'add', '-A');
  G(work, 'commit', '-m', 'init');
  G(work, 'push', '-u', 'origin', 'main');
  G(root, 'clone', bare, other);
  G(other, 'config', 'user.email', 'o@example.com');
  G(other, 'config', 'user.name', 'other');
  return { root, bare, work, other };
}

const git_health_of = (dir) => git.health(dir);

(async () => {
  await test('health: 正常なら ok と平易な一文', async () => {
    const { work } = scaffold();
    const h = await git.health(work);
    assert.strictEqual(h.level, 'ok');
    assert.match(h.summary, /正常/);
  });

  await test('health: 非 git は notRepo（エラーにしない）', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-heal-plain-'));
    const h = await git.health(dir);
    assert.strictEqual(h.notRepo, true);
  });

  await test('health: 食い違い（ahead+behind）は error として言い切る', async () => {
    const { work, other } = scaffold();
    fs.writeFileSync(path.join(other, 'remote.md'), 'r\n');
    G(other, 'add', '-A');
    G(other, 'commit', '-m', 'remote change');
    G(other, 'push', 'origin', 'main');
    fs.writeFileSync(path.join(work, 'local.md'), 'l\n');
    G(work, 'add', '-A');
    G(work, 'commit', '-m', 'local change');
    G(work, 'fetch', 'origin', 'main');
    const h = await git.health(work);
    assert.strictEqual(h.level, 'error');
    assert.match(h.summary, /食い違って/);
  });

  await test('heal: 遅れは取り込み・未送信は送信し、正常へ戻す', async () => {
    const { work, other } = scaffold();
    fs.writeFileSync(path.join(other, 'remote.md'), 'r\n');
    G(other, 'add', '-A');
    G(other, 'commit', '-m', 'remote change');
    G(other, 'push', 'origin', 'main');
    fs.writeFileSync(path.join(work, 'local.md'), 'l\n');
    G(work, 'add', '-A');
    G(work, 'commit', '-m', 'local change');

    const res = await git.heal(work);
    assert.strictEqual(res.level, 'ok', JSON.stringify(res));
    assert.ok(fs.existsSync(path.join(work, 'remote.md')), 'リモートの変更を取得');
    assert.strictEqual(G(work, 'rev-list', '--count', 'origin/main..HEAD'), '0', '未送信ゼロ');
    const h = await git.health(work);
    assert.strictEqual(h.level, 'ok');
  });

  await test('heal: 中断 rebase の残骸を巻き戻してから進める', async () => {
    const { work, other } = scaffold();
    // 両側が同じファイルを書いて rebase を確実に止める → 中断状態を作る
    fs.writeFileSync(path.join(other, 'journal.md'), 'remote line\n');
    G(other, 'add', '-A');
    G(other, 'commit', '-m', 'remote');
    G(other, 'push', 'origin', 'main');
    fs.writeFileSync(path.join(work, 'journal.md'), 'local line\n');
    G(work, 'add', '-A');
    G(work, 'commit', '-m', 'local');
    G(work, 'fetch', 'origin', 'main');
    try {
      G(work, 'rebase', 'origin/main');       // 必ずコンフリクトで止まる
    } catch {
      /* 中断状態のまま放置 = 前回の同期が途中で死んだ状況 */
    }
    const before = await git.health(work);
    assert.strictEqual(before.level, 'error');
    assert.match(before.summary, /途中で止まって/);

    const res = await git.heal(work);
    // 巻き戻し（abort）はしたが、同一ファイルの両側編集は自動では直さない（安全側）
    assert.ok(res.steps.some((s) => /巻き戻し/.test(s)), JSON.stringify(res));
    const after = await git.health(work);
    assert.ok(!after.midRebase, 'rebase の残骸が残らない');
  });

  await test('heal: 書き込み中（dirty）の食い違いは合流を見送り、理由を言う', async () => {
    const { work, other } = scaffold();
    fs.writeFileSync(path.join(other, 'remote.md'), 'r\n');
    G(other, 'add', '-A');
    G(other, 'commit', '-m', 'remote');
    G(other, 'push', 'origin', 'main');
    fs.writeFileSync(path.join(work, 'local.md'), 'l\n');
    G(work, 'add', '-A');
    G(work, 'commit', '-m', 'local');
    fs.writeFileSync(path.join(work, 'journal.md'), 'writing...\n'); // 書き込み中を模す

    const res = await git.heal(work);
    assert.strictEqual(res.level, 'warn');
    assert.match(res.summary, /見送り/);
    // 人の未コミット変更は無傷
    assert.strictEqual(fs.readFileSync(path.join(work, 'journal.md'), 'utf8'), 'writing...\n');
  });

  await test('worktree の中断 rebase も検知して巻き戻せる（.git がファイルの地雷）', async () => {
    // agent-project の状態 worktree では .git がファイルなので、<top>/.git/rebase-merge を
    // 直接見る実装は永遠に検知できない（🩺 が「解決しない」ボタンになっていた実障害）。
    const { root, work, other } = scaffold();
    const wt = path.join(root, 'wt');
    G(work, 'worktree', 'add', '-b', 'agent-state', wt);
    // worktree 側と origin/main を同じファイルの両側編集で分岐させ、rebase を確実に止める
    fs.writeFileSync(path.join(other, 'journal.md'), 'remote line\n');
    G(other, 'add', '-A');
    G(other, 'commit', '-m', 'remote');
    G(other, 'push', 'origin', 'main');
    fs.writeFileSync(path.join(wt, 'journal.md'), 'wt line\n');
    G(wt, 'add', '-A');
    G(wt, 'commit', '-m', 'wt local');
    G(wt, 'fetch', 'origin', 'main');
    try {
      G(wt, 'rebase', 'origin/main');       // 必ずコンフリクトで止まる
    } catch {
      /* 中断状態のまま放置 */
    }
    const before = await git_health_of(wt);
    assert.strictEqual(before.midRebase, true, 'worktree でも中断 rebase を検知する');
    const res = await git.heal(wt);
    assert.ok(res.steps.some((s) => /巻き戻し/.test(s)), JSON.stringify(res));
    const after = await git_health_of(wt);
    assert.ok(!after.midRebase, '巻き戻し後に残骸が残らない');
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
