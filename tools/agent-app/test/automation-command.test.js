'use strict';

// 外部コマンドの起動仕様。Windows でここを間違えると、記録がまったく始まらない。
//   - npm のグローバル導入は `playwright-cli.cmd`、winauto の Windows インストーラは
//     `winauto.bat` を置く。Node の spawn が PATH で補うのは `.exe` だけなので、
//     名前だけでは ENOENT になる（実際、記録が始まらない原因がこれだった）。
//   - 見つけても `.cmd` / `.bat` は直接 spawn できないので `cmd /d /s /c` に載せる。

const { test } = require('node:test');
const assert = require('node:assert');
const command = require('../src/main/automation/command');

const ENV = { PATH: 'C:\\npm;C:\\Windows', PATHEXT: '.COM;.EXE;.BAT;.CMD', COMSPEC: 'cmd.exe' };
const FILES = new Set(['C:\\npm\\playwright-cli.cmd', 'C:\\npm\\winauto.bat', 'C:\\Windows\\python.exe']);
const exists = (f) => FILES.has(f);
const win = (name, args) => command.spawnSpec(name, args, { platform: 'win32', env: ENV, exists });

test('Windows: npm が置く .cmd を PATHEXT で見つける', () => {
  assert.strictEqual(command.windowsExecutable('playwright-cli', { env: ENV, exists }), 'C:\\npm\\playwright-cli.cmd');
  assert.strictEqual(command.windowsExecutable('winauto', { env: ENV, exists }), 'C:\\npm\\winauto.bat');
  assert.strictEqual(command.windowsExecutable('nope', { env: ENV, exists }), '');
});

test('Windows: .cmd / .bat は cmd に載せ、引数は引用してそのまま渡す', () => {
  const spec = win('playwright-cli', ['-s=rec', 'open', '--headed', 'http://a/b?c=1&d=2']);
  assert.strictEqual(spec.command, 'cmd.exe');
  assert.deepStrictEqual(spec.args.slice(0, 3), ['/d', '/s', '/c']);
  assert.strictEqual(spec.args[3],
    '""C:\\npm\\playwright-cli.cmd" "-s=rec" "open" "--headed" "http://a/b?c=1&d=2""');
  assert.strictEqual(spec.options.windowsVerbatimArguments, true, 'Node に引用を足させない');
  assert.strictEqual(spec.options.shell, false, 'シェルは介さない（引用が二重に解釈される）');
});

test('Windows: .exe はそのまま起動する', () => {
  const spec = win('python', ['--version']);
  assert.strictEqual(spec.command, 'C:\\Windows\\python.exe');
  assert.deepStrictEqual(spec.args, ['--version']);
  assert.ok(!spec.options.windowsVerbatimArguments);
});

test('Windows: 見つからない名前はそのまま渡す（起動に失敗して理由が出る）', () => {
  assert.strictEqual(win('nope', ['x']).command, 'nope');
});

test('Windows 以外は名前をそのまま渡す（解決は OS に任せる）', () => {
  const spec = command.spawnSpec('playwright-cli', ['open'], { platform: 'linux', cwd: '/tmp' });
  assert.strictEqual(spec.command, 'playwright-cli');
  assert.deepStrictEqual(spec.args, ['open']);
  assert.strictEqual(spec.options.cwd, '/tmp');
});

test('起動の口は既定で同じ仕様を通り、差し替えても spec.command 以外を素で spawn しない', () => {
  const src = require('fs').readFileSync(require('path').join(__dirname, '..', 'src', 'main', 'automation', 'runner.js'), 'utf8');
  // capture / stream / startDetached は `spawnSpec` オプションで起動仕様を差し替えられる
  // （既定値は command.spawnSpec）。埋め込む側が特定のコマンドだけ別経路（WSL のログイン
  // シェル等）へ載せ替えるための口で、既定のままなら Windows の .cmd / .bat 解決を必ず通る。
  for (const fn of ['function capture', 'function stream', 'function startDetached']) {
    const body = src.slice(src.indexOf(fn), src.indexOf(fn) + 600);
    assert.match(body, /spawnSpec\s*=\s*command\.spawnSpec/, `${fn} の既定が command.spawnSpec を通っていない`);
    assert.match(body, /=\s*spawnSpec\(name,\s*args/, `${fn} が差し替え可能な spawnSpec を呼んでいない`);
  }
  // spawnRecorder（winauto 専用）は差し替えの対象にしていない。常に command.spawnSpec のまま。
  const recorderBody = src.slice(src.indexOf('function spawnRecorder'), src.indexOf('function spawnRecorder') + 600);
  assert.match(recorderBody, /command\.spawnSpec\(/, 'spawnRecorder が起動仕様を通っていない');
  assert.ok(!/spawn\((?!spec\.command)/.test(src.replace(/require\('child_process'\)/, '')), '素の spawn が残っている');
});
