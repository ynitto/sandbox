'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const cowork = require('../src/features/cowork/main/cowork');
const {
  makeLoopProvider, winDriveToWsl, toWslCwd, sh: providerSh,
} = require('../src/features/cowork/main/loopProvider');

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

test('loop 実行は kiro-loop の send サブコマンドでプロンプト名を送る（run は存在しない）', () => {
  // command を echo に差し替えて、組み立てられた引数だけを検証する
  const r = makeLoopProvider({ loopCommand: 'echo' }).run({ id: '毎朝レビュー', cwd: os.tmpdir() });
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send 毎朝レビュー');
});

test('loop 実行は明示 args があればそれを優先する', () => {
  const r = makeLoopProvider({ loopCommand: 'echo' }).run({ id: 'X', args: ['send', '-s', 'sess', 'X'], cwd: os.tmpdir() });
  assert.ok(r.ok);
  assert.strictEqual(r.stdout, 'send -s sess X');
});

test('winDriveToWsl は Windows ドライブパスを /mnt/<drive> に変換する', () => {
  assert.strictEqual(winDriveToWsl('C:\\proj\\アプリ'), '/mnt/c/proj/アプリ');
  assert.strictEqual(winDriveToWsl('D:/work/repo/'), '/mnt/d/work/repo');
  assert.strictEqual(winDriveToWsl('C:\\'), '/mnt/c');
  assert.strictEqual(winDriveToWsl('/home/me/repo'), '');          // POSIX は対象外
  assert.strictEqual(winDriveToWsl('\\\\wsl.localhost\\U\\home'), '');
});

test('toWslCwd は UNC/ドライブ/POSIX を WSL 側パスへ寄せる', () => {
  assert.strictEqual(toWslCwd('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(toWslCwd('C:\\proj'), '/mnt/c/proj');
  assert.strictEqual(toWslCwd('/home/me/repo'), '/home/me/repo');
});

test('win32 では Windows ドライブ上のリポジトリでも wsl.exe 経由で loop を実行する（直接 spawn しない）', () => {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    const r = providerSh('kiro-loop', ['send', 'x'], { cwd: 'C:\\proj\\app' });
    // Linux 上のテストでは wsl.exe が無く ENOENT になるが、その ENOENT が
    // kiro-loop ではなく wsl.exe を指していること＝WSL 経由であることを検証する。
    assert.ok(/wsl\.exe/.test(r.error), `wsl.exe を起動する: ${r.error}`);
    assert.ok(!/spawnSync kiro-loop/.test(r.error), 'kiro-loop を Windows 側で直接 spawn しない');
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
});

test('state-machine 実行は statemachine-use スキルを発動するプロンプトを kiro-loop send で送る', () => {
  const repo = os.tmpdir();
  const config = { cowork: {
    loopCommand: 'echo',
    items: [{ id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo }],
  } };
  const r = cowork.runStateMachine(config, 'sm1', '');
  assert.ok(r.ok, `echo が成功する: ${r.error || r.stderr}`);
  assert.strictEqual(r.stdout, 'send release ステートマシンを実行して');
  const withInput = cowork.runStateMachine(config, 'sm1', 'v1.2');
  assert.strictEqual(withInput.stdout, 'send release ステートマシンを実行して。入力: v1.2');
});

test('runLoop / runStateMachine は実行履歴（historyFile）へ記録し readHistory で新しい順に読める', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-hist-'));
  const historyFile = path.join(repo, 'history.jsonl');
  const config = { cowork: {
    loopCommand: 'echo',
    historyFile,
    items: [
      { id: 'daily', type: 'loop', name: '毎朝レビュー', repo },
      { id: 'sm1', type: 'state-machine', name: 'リリース', workflow: 'release', repo },
    ],
  } };
  assert.ok(cowork.runLoop(config, 'daily').ok);
  assert.ok(cowork.runStateMachine(config, 'sm1', '').ok);
  assert.ok(cowork.runLoop(config, 'daily').ok);

  const loopLogs = cowork.itemLogs(config, 'daily');
  assert.strictEqual(loopLogs.history.length, 2);
  assert.ok(loopLogs.history.every((h) => h.ok && h.name === '毎朝レビュー' && h.type === 'loop'));
  assert.ok(loopLogs.history[0].at >= loopLogs.history[1].at, '新しい順');
  const smLogs = cowork.itemLogs(config, 'sm1');
  assert.strictEqual(smLogs.history.length, 1);
  assert.strictEqual(smLogs.history[0].type, 'state-machine');
  assert.match(smLogs.history[0].message, /send release-runner|send release/);
});

test('itemLogs はリポジトリのログ候補を返し readLog は末尾を読む（候補外パスは拒否）', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-logs-'));
  fs.mkdirSync(path.join(repo, '.kiro-loop', 'logs'), { recursive: true });
  const logFile = path.join(repo, '.kiro-loop', 'logs', 'run.log');
  fs.writeFileSync(logFile, `${'x'.repeat(3000)}TAIL-MARKER\n`);
  const secret = path.join(repo, 'secret.txt');
  fs.writeFileSync(secret, 'top secret');
  const config = { cowork: {
    historyFile: path.join(repo, 'history.jsonl'),
    items: [{ id: 'daily', type: 'loop', name: 'daily', repo }],
  } };
  const info = cowork.itemLogs(config, 'daily');
  assert.strictEqual(info.logs.length, 1);
  assert.strictEqual(info.logs[0].name, 'run.log');
  assert.ok(info.logs[0].size > 3000);
  const read = cowork.readLog(config, 'daily', info.logs[0].file, 2000);
  assert.ok(read.text.includes('TAIL-MARKER'), '末尾を読む');
  assert.ok(read.truncated, '上限超は truncated');
  assert.throws(() => cowork.readLog(config, 'daily', secret), /この作業のログではありません/);
});

test('実行履歴は上限を超えると新しい方だけ残して切り詰める', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cowork-trim-'));
  const historyFile = path.join(dir, 'history.jsonl');
  const cfg = { historyFile };
  for (let i = 0; i < 1005; i += 1) {
    cowork.appendHistory(cfg, { at: new Date(2026, 0, 1, 0, 0, i % 60).toISOString(), key: 'k', ok: true, message: `run-${i}` });
  }
  const lines = fs.readFileSync(historyFile, 'utf8').split('\n').filter(Boolean);
  assert.ok(lines.length <= 600, `切り詰められている: ${lines.length}`);
  assert.ok(lines[lines.length - 1].includes('run-1004'), '最新は残る');
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
