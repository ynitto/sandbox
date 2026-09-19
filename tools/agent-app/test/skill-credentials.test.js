'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { preparePatch, readToken, gitExec } = require('../src/main/skillCredentials');
const { ArtifactShare, cloneDir } = require('../src/main/artifactShare');
const { SkillPublication } = require('../src/main/skillPublication');
const store = require('../src/main/store');

const token = 'test-only-token';
const url = 'https://example.test/team/skills.git';
const safeStorage = {
  isEncryptionAvailable: () => true,
  encryptString: (value) => { assert.equal(value, token); return Buffer.from('os-encrypted-value'); },
  decryptString: (value) => { assert.equal(value.toString(), 'os-encrypted-value'); return token; },
};

test('公開先・暗号化トークン・選択を保存し、監査の部分更新後も再起動相当の読み込みで保持する', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-settings-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const patch = preparePatch({ audit: { shareRepo: url, shareToken: token, skillRepo: '/repo', skillAgent: 'codex' } }, {}, safeStorage);
  store.saveConfig(dir, patch);
  store.saveConfig(dir, { audit: { intervalMinutes: 30 } });
  const restored = store.loadConfig(dir);
  assert.equal(restored.audit.shareRepo, url);
  assert.equal(restored.audit.skillRepo, '/repo');
  assert.equal(restored.audit.skillAgent, 'codex');
  assert.equal(readToken(restored, safeStorage), token);
  assert.equal(fs.readFileSync(path.join(dir, 'config.json'), 'utf8').includes(token), false);
  assert.equal(Object.hasOwn(restored.audit, 'shareToken'), false);
  store.saveConfig(dir, preparePatch({ audit: { shareToken: '' } }, restored.audit, safeStorage));
  assert.equal(readToken(store.loadConfig(dir), safeStorage), '');
});

test('暗号化できない場合とHTTPS以外のトークン入力は保存しない', () => {
  assert.throws(() => preparePatch({ audit: { shareRepo: url, shareToken: token } }, {}, {
    isEncryptionAvailable: () => false,
  }), /暗号化/);
  assert.throws(() => preparePatch({ audit: { shareRepo: 'git@example.test:repo.git', shareToken: token } }, {}, safeStorage), /HTTPS/);
  assert.throws(() => readToken({ audit: { shareTokenEncrypted: 'bad' } }, safeStorage), /入力し直して/);
});

test('認証情報をURLやargvに埋め込まず、出力も伏せる。トークンなしは既存のGit認証を使う', async () => {
  const scripts = [];
  const calls = [];
  const encoded = Buffer.from(`oauth2:${token}`).toString('base64');
  const shell = {
    run: async (script) => { scripts.push(script); return { ok: false, error: `${token} ${encoded}`, output: `Authorization: Basic ${encoded}` }; },
    exec: async (argv) => { calls.push(argv); return { ok: true }; },
  };
  const argv = ['git', 'clone', url, '/tmp/example'];
  const result = await gitExec(shell, argv, {}, { url, token });
  assert.equal(calls.length, 0);
  assert.equal(argv.includes(token), false);
  assert.ok(scripts[0].includes(`http.${url}.extraHeader`));
  assert.ok(scripts[0].includes('credential.helper'));
  assert.ok(!scripts[0].includes(token));
  assert.ok(!result.error.includes(token) && !result.error.includes(encoded));
  assert.ok(!result.output.includes(encoded));
  await gitExec(shell, argv, {}, { url });
  assert.deepEqual(calls, [argv]);
});

test('公開操作のclone・fetch・pushのすべてで保存トークンを使用する', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-token-push-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  const skill = path.join(repo, '.agents/skills/review');
  fs.mkdirSync(skill, { recursive: true });
  fs.writeFileSync(path.join(skill, 'SKILL.md'), '# review');
  const scripts = [];
  const shell = { run: async (script) => {
    scripts.push(script);
    if (script.includes("'diff' '--cached'")) return { ok: false, status: 1 };
    if (script.includes("'ls-remote'")) return { ok: true, output: 'ref: refs/heads/main\tHEAD\nabc123\tHEAD' };
    return { ok: true, output: script.includes("'symbolic-ref'") ? 'origin/main' : '' };
  } };
  const share = new ArtifactShare({ userData: root, loadConfig: () => ({ audit: { shareRepo: url } }), loadToken: () => token, shellFor: () => shell });
  await share.submit({ repo, kind: 'skill', name: 'review' });
  fs.mkdirSync(path.join(cloneDir(root, url), '.git'), { recursive: true });
  await share.ensureClone(url);
  for (const command of ['clone', 'fetch', 'push']) {
    assert.ok(scripts.some((script) => script.includes(`'${command}'`) && script.includes('GIT_CONFIG_VALUE_0=')), command);
  }
  assert.ok(!fs.readFileSync(path.join(root, 'artifact-share/state.json'), 'utf8').includes(token));
});

test('公開状態の照合は設定した公開先だけにトークンを渡す', async () => {
  const scripts = []; const calls = [];
  const publication = new SkillPublication({
    userData: '/unused', loadAuth: () => ({ url, token }),
    shell: () => ({
      run: async (script) => { scripts.push(script); return { ok: true }; },
      exec: async (argv) => { calls.push(argv); return { ok: true }; },
    }),
  });
  await publication.exec(['git', 'clone', url, '/unused'], url);
  assert.equal(scripts.length, 1);
  const other = 'https://other.test/team/skills.git';
  await publication.exec(['git', 'clone', other, '/unused'], other);
  assert.equal(scripts.length, 1);
  assert.equal(calls.length, 1);
});
