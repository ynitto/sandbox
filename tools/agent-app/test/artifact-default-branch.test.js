'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { ArtifactShare } = require('../src/main/artifactShare');

test('既存クローンが main を覚えていても、変更後のデフォルトブランチへ公開する', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-default-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  const remote = path.join(root, 'remote.git');
  fs.mkdirSync(repo);
  const git = (cwd, ...args) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git(repo, 'init', '-b', 'main');
  git(repo, 'config', 'user.name', 'Test'); git(repo, 'config', 'user.email', 'test@example.test');
  fs.writeFileSync(path.join(repo, 'README.md'), 'base');
  git(repo, 'add', '.'); git(repo, 'commit', '-m', 'base');
  execFileSync('git', ['clone', '--bare', repo, remote], { stdio: 'pipe' });
  const shell = {
    exec: async (argv) => {
      try { return { ok: true, output: execFileSync(argv[0], argv.slice(1), { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }) }; }
      catch (e) { return { ok: false, error: String(e.stderr), status: e.status }; }
    },
    run: async (script) => shell.exec(['sh', '-c', script]),
  };
  const share = new ArtifactShare({ userData: path.join(root, 'data'), shellFor: () => shell,
    loadConfig: () => ({ audit: { shareRepo: remote, pushToMain: true } }),
  });
  await share.ensureClone(remote);
  const original = git(remote, 'rev-parse', 'main');
  git(remote, 'branch', 'release/stable', 'main');
  git(remote, 'symbolic-ref', 'HEAD', 'refs/heads/release/stable');
  const dir = path.join(repo, '.agents/skills/review');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'SKILL.md'), '# review');
  const result = await share.submit({ repo, kind: 'skill', name: 'review' });
  assert.equal(result.branch, 'release/stable');
  assert.equal(git(remote, 'rev-parse', 'main'), original);
  assert.equal(git(remote, 'show', 'release/stable:.agents/skills/review/SKILL.md'), '# review');
  // 一覧が共通の保存先を指す場合、同名のrepo側を誤って公開しない。
  const home = path.join(root, 'home/.claude/skills/review');
  fs.mkdirSync(home, { recursive: true });
  fs.writeFileSync(path.join(home, 'SKILL.md'), '# selected home skill');
  const { sourceOf, SkillPublication } = require('../src/main/skillPublication');
  const item = { name: 'review', dir: home, path: path.join(home, 'SKILL.md'), place: 'home' };
  const source = sourceOf(item);
  const publication = new SkillPublication({ userData: path.join(root, 'data'), shell: () => shell });
  assert.equal(publication.present(item, { configured: true }, { versionComparison: 'local-newer' }, !!source).canPublish, true);
  await share.submit({ repo, kind: 'skill', name: 'review', source, force: true });
  assert.equal(git(remote, 'show', 'release/stable:.agents/skills/review/SKILL.md'), '# selected home skill');
  assert.equal(fs.readFileSync(path.join(dir, 'SKILL.md'), 'utf8'), '# review');

});

test('デフォルトブランチを確認できない場合は main を推測しない', async () => {
  const share = new ArtifactShare({ userData: '/unused' });
  share.git = async () => ({ ok: true, out: '' });
  await assert.rejects(() => share.defaultBranch('/unused'), /デフォルトブランチ/);
});
