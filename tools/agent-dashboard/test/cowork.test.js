'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const cowork = require('../src/features/cowork/main/cowork');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-repo-'));
  spawnSync('git', ['init', '-b', 'main'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['config', 'user.email', 'cowork@example.test'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['config', 'user.name', 'Cowork Test'], { cwd: repo, encoding: 'utf8' });
  fs.writeFileSync(path.join(repo, 'README.md'), '# repo\n');
  spawnSync('git', ['add', 'README.md'], { cwd: repo, encoding: 'utf8' });
  spawnSync('git', ['commit', '-m', 'init'], { cwd: repo, encoding: 'utf8' });
  return repo;
}

test('itemsOf は cowork.items だけを正として扱い旧 loopJobs/stateMachines は読まない', () => {
  const items = cowork.itemsOf({
    items: [{ id: 'flat', type: 'loop', repo: '/repo-a' }],
    loopJobs: [{ id: 'legacy-loop', cwd: '/repo-b' }],
    stateMachines: [{ id: 'legacy-sm', cwd: '/repo-c' }],
  });
  assert.deepStrictEqual(items.map((x) => x.id), ['flat']);
});

test('overview は複数リポジトリの作業をフラットに並べる', () => {
  const repoA = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-a-'));
  const repoB = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-b-'));
  fs.mkdirSync(path.join(repoA, '.kiro-loop', 'logs'), { recursive: true });
  fs.mkdirSync(path.join(repoB, '.statemachine-use', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repoA, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  fs.writeFileSync(path.join(repoB, '.statemachine-use', 'logs', 'flow.log'), 'idle\n');
  const ov = cowork.overview({ cowork: { items: [
    { id: 'daily', type: 'loop', repo: repoA },
    { id: 'release', type: 'state-machine', repo: repoB, workflow: 'release.yaml' },
  ] } });
  assert.deepStrictEqual(ov.items.map((x) => x.repo), [repoA, repoB]);
  assert.deepStrictEqual(ov.items.map((x) => x.type), ['loop', 'state-machine']);
});

test('overview は statusFile を作らず既存ログとプロセス由来の state を返す', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  const ov = cowork.overview({ cowork: { items: [{ id: 'daily', type: 'loop', repo }] } });
  assert.strictEqual(ov.items.length, 1);
  assert.strictEqual(ov.items[0].state.status, 'done');
  assert.ok(ov.items[0].state.lastLog.endsWith('run.log'));
  assert.ok(!fs.existsSync(path.join(repo, 'status.json')));
});

test('saveWork は複数リポジトリそれぞれに git 保存処理を試みる', () => {
  const repoA = tmpRepo();
  const repoB = tmpRepo();
  const saved = [];
  const res = cowork.saveWork({}, (cfg) => { saved.push(cfg); return cfg; }, {
    items: [
      { id: 'a', type: 'loop', repo: repoA },
      { id: 'b', type: 'state-machine', repo: repoB },
    ],
  });
  assert.strictEqual(saved.length, 1);
  assert.deepStrictEqual(res.git.map((x) => x.repo).sort(), [repoA, repoB].sort());
  assert.ok(res.git.every((x) => x.result.ok));
});

test('wslPath は WSL UNC を Linux パスへ変換する', () => {
  assert.strictEqual(cowork.wslPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(cowork.wslPath('/home/me/repo'), '/home/me/repo');
});

test('decodeCliOutput は不正 UTF-8 を Shift_JIS として読む', () => {
  // CP932 の「あ」(0x82 0xA0)
  const buf = Buffer.from([0x82, 0xa0]);
  assert.strictEqual(cowork.decodeCliOutput(buf), 'あ');
  assert.strictEqual(cowork.decodeCliOutput(Buffer.from('ok', 'utf8')), 'ok');
});

test('overview の既定はプロセス探査せず probed=false', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-light-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro-loop', 'logs', 'run.log'), 'finished successfully\n');
  const ov = cowork.overview({ cowork: { items: [{ id: 'daily', type: 'loop', repo }] } });
  assert.strictEqual(ov.items[0].state.probed, false);
  assert.strictEqual(ov.items[0].state.running, false);
  const probed = cowork.overview(
    { cowork: { items: [{ id: 'daily', type: 'loop', repo }] } },
    { probeProcess: true }
  );
  assert.strictEqual(probed.items[0].state.probed, true);
});

console.log(`\n${passed} cowork tests passed`);
