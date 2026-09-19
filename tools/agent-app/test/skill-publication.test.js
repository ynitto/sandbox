'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { SkillPublication } = require('../src/main/skillPublication');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-publication-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  const remote = path.join(root, 'remote.git');
  const git = (cwd, ...args) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  fs.mkdirSync(repo);
  git(repo, 'init', '-b', 'main');
  git(repo, 'config', 'user.name', 'Test');
  git(repo, 'config', 'user.email', 'test@example.test');
  const dir = path.join(repo, '.github/skills/review');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'SKILL.md'), '# review\n');
  fs.writeFileSync(path.join(dir, 'helper.py'), 'print(1)\n');
  git(repo, 'add', '.');
  git(repo, 'commit', '-m', 'skill');
  execFileSync('git', ['clone', '--bare', repo, remote], { stdio: 'pipe' });
  git(repo, 'remote', 'add', 'origin', remote);
  const shell = { exec: async (argv) => {
    try { return { ok: true, output: execFileSync(argv[0], argv.slice(1), { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }) }; }
    catch (e) { return { ok: false, error: String(e.stderr || e.message) }; }
  } };
  const publication = new SkillPublication({ userData: path.join(root, 'data'), shell: () => shell });
  const item = { name: 'review', path: path.join(dir, 'SKILL.md'), dir, place: 'repo', repo };
  const states = (items = [item], shareRepo = remote) => publication.states(items, shareRepo);
  return { root, repo, remote, dir, git, item, states, publication };
}

test('アプリの履歴がなくても Git に push 済みなら公開済み。共通のコピーでも変わらない', async (t) => {
  const f = fixture(t);
  const homeDir = path.join(f.root, 'home/.agents/skills/review');
  fs.cpSync(f.dir, homeDir, { recursive: true });
  const home = { ...f.item, place: 'home', repo: '', dir: homeDir, path: path.join(homeDir, 'SKILL.md') };
  assert.equal((await f.states()).get(f.item.path).status, 'published');
  assert.equal((await f.states([home])).get(home.path).status, 'published');
  fs.writeFileSync(path.join(homeDir, 'helper.py'), 'print(2)\n');
  assert.equal((await f.states([home])).get(home.path).status, 'updated');
});

test('ローカルだけのコミット・追加・削除は公開済みにしない', async (t) => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.dir, 'helper.py'), 'print(2)\n');
  f.git(f.repo, 'add', '.'); f.git(f.repo, 'commit', '-m', 'local only');
  assert.equal((await f.states()).get(f.item.path).status, 'updated');
  fs.writeFileSync(path.join(f.dir, 'helper.py'), 'print(1)\n');
  fs.writeFileSync(path.join(f.dir, 'extra.txt'), 'new');
  assert.equal((await f.states()).get(f.item.path).status, 'updated');
  fs.unlinkSync(path.join(f.dir, 'extra.txt'));
  fs.unlinkSync(path.join(f.dir, 'helper.py'));
  assert.equal((await f.states()).get(f.item.path).status, 'updated');
});

test('git-skill-manager の除外ファイルや改行コードだけでは変更扱いにしない', async (t) => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.dir, '.DS_Store'), 'local');
  fs.mkdirSync(path.join(f.dir, '__pycache__'));
  fs.writeFileSync(path.join(f.dir, '__pycache__/helper.pyc'), 'local');
  fs.writeFileSync(f.item.path, '# review\r\n');
  assert.equal((await f.states()).get(f.item.path).status, 'published');
});

test('公開先変更と接続失敗を未公開や公開済みに誤変換しない', async (t) => {
  const f = fixture(t);
  assert.equal((await f.states()).get(f.item.path).status, 'published');
  const failed = await f.states([f.item], path.join(f.root, 'missing.git'));
  assert.equal(failed.get(f.item.path).status, 'unknown');
});

test('公開先が未設定でも git-skill-manager の登録先と独自 skill_root を参照する', async (t) => {
  const f = fixture(t);
  const home = path.join(f.root, 'home/.agents');
  const dir = path.join(home, 'skills/review');
  fs.cpSync(f.dir, dir, { recursive: true });
  fs.mkdirSync(path.join(f.repo, 'custom'), { recursive: true });
  f.git(f.repo, 'mv', '.github/skills', 'custom/skills');
  f.git(f.repo, 'commit', '-m', 'custom root');
  f.git(f.repo, 'push', 'origin', 'main');
  fs.writeFileSync(path.join(home, 'skill-registry.json'), JSON.stringify({
    repositories: [{ name: 'team', url: f.remote, branch: 'main', skill_root: 'custom/skills' }],
    installed_skills: [{ name: 'review', source_repo: 'team', source_path: 'custom/skills/review' }],
  }));
  const item = { ...f.item, dir, path: path.join(dir, 'SKILL.md'), place: 'home', repo: '' };
  assert.equal((await f.states([item], '')).get(item.path).status, 'published');
});

test('git-skill-manager が作る公開ブランチと、更新後の fetch を照合する', async (t) => {
  const f = fixture(t);
  assert.equal((await f.states()).get(f.item.path).status, 'published');
  f.git(f.repo, 'checkout', '-b', 'add-skill/review');
  fs.writeFileSync(path.join(f.dir, 'helper.py'), 'print(3)\n');
  f.git(f.repo, 'add', '.'); f.git(f.repo, 'commit', '-m', 'new version');
  f.git(f.repo, 'push', 'origin', 'add-skill/review');
  f.publication.cache.clear();
  const state = (await f.states()).get(f.item.path);
  assert.equal(state.status, 'published');
  assert.equal(state.branch, 'add-skill/review');
});

test('一覧の状態と操作可否は公開先の証拠と実際の参照先に従う', async (t) => {
  const f = fixture(t);
  const base = { status: 'unpublished', configured: true, canPublish: true, canImprove: true };
  const published = (await f.states()).get(f.item.path);
  assert.equal(f.publication.present(f.item, base, published, true).canPublish, false);
  assert.equal(f.publication.present(f.item, base, { status: 'updated', versionComparison: 'local-newer' }, true).canPublish, true);
  assert.equal(f.publication.present(f.item, base, { status: 'updated', versionComparison: 'local-newer' }, false).canPublish, false);
  for (const versionComparison of ['same', 'remote-newer', 'unknown']) {
    assert.equal(f.publication.present(f.item, base, { status: 'updated', versionComparison }, true).canPublish, false);
  }
  const failed = f.publication.present(f.item, base, { status: 'unknown' }, true);
  assert.equal(failed.canPublish, false);
  assert.equal(failed.canImprove, false);
});

test('公開先に存在しないスキルと、違う公開先の同名スキルを区別する', async (t) => {
  const f = fixture(t);
  const remote = path.join(f.root, 'other.git');
  f.git(f.repo, 'rm', '-r', '.github/skills/review');
  f.git(f.repo, 'commit', '-m', 'no skill');
  execFileSync('git', ['clone', '--bare', f.repo, remote], { stdio: 'pipe' });
  f.git(f.repo, 'checkout', 'HEAD~1', '--', '.github/skills/review');
  assert.equal((await f.states()).get(f.item.path).status, 'published');
  assert.equal((await f.states([f.item], remote)).get(f.item.path).status, 'unpublished');
});

test('接続失敗を一覧の件数だけ再試行せず、次の読み込みでは再試行する', async (t) => {
  const f = fixture(t);
  let calls = 0;
  f.publication.shell = () => ({ exec: async () => { calls++; return { ok: false, error: 'offline' }; } });
  const items = [f.item, { ...f.item, name: 'other', path: 'other/SKILL.md' }];
  const first = await f.states(items);
  assert.equal(calls, 1);
  assert.equal(first.get('other/SKILL.md').status, 'unknown');
  await f.states(items);
  assert.equal(calls, 2);
});

test('同名スキルの版を比較し、同じ版でも内容の差は残す', async (t) => {
  const f = fixture(t);
  const body = (version) => `---\nmetadata:\n  version: ${version}\n---\n# review\n`;
  fs.writeFileSync(f.item.path, body('1.9.0'));
  f.git(f.repo, 'add', '.'); f.git(f.repo, 'commit', '-m', 'version'); f.git(f.repo, 'push', 'origin', 'main');
  for (const [version, comparison, status] of [
    ['1.10.0', 'local-newer', 'updated'], ['1.8.0', 'remote-newer', 'updated'], ['1.9.0', 'same', 'published'],
  ]) {
    fs.writeFileSync(f.item.path, body(version));
    const state = (await f.states()).get(f.item.path);
    assert.equal(state.localVersion, version);
    assert.equal(state.remoteVersion, '1.9.0');
    assert.equal(state.versionComparison, comparison);
    assert.equal(state.status, status);
  }
  fs.writeFileSync(path.join(f.dir, 'helper.py'), 'print(99)\n');
  const changed = (await f.states()).get(f.item.path);
  assert.equal(changed.versionComparison, 'same');
  assert.equal(changed.status, 'updated');
});

test('古いブランチで内容が一致しても、公開先の新しい版を比較に使う', async (t) => {
  const f = fixture(t);
  fs.writeFileSync(f.item.path, '---\nversion: 1.0.0\n---\n# review\n');
  f.git(f.repo, 'add', '.'); f.git(f.repo, 'commit', '-m', 'v1'); f.git(f.repo, 'push', 'origin', 'main');
  f.git(f.repo, 'checkout', '-b', 'release/new');
  fs.writeFileSync(f.item.path, '---\nversion: 2.0.0\n---\n# review\n');
  f.git(f.repo, 'add', '.'); f.git(f.repo, 'commit', '-m', 'v2'); f.git(f.repo, 'push', 'origin', 'release/new');
  f.git(f.repo, 'checkout', 'main');
  const state = (await f.states()).get(f.item.path);
  assert.equal(state.status, 'published');
  assert.equal(state.remoteVersion, '2.0.0');
  assert.equal(state.remoteVersionBranch, 'release/new');
  assert.equal(state.versionComparison, 'remote-newer');
});
