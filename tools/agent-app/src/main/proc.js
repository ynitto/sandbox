'use strict';

// 子プロセスの起こし方と終わらせ方。Windows では CLI が WSL に居るので、その差をここで吸収する。
// 会話（ヘッドレス）・共有・セッション ID の拾い直しが同じ作法で起動できるように 1 か所へ置く。

const { execFile } = require('child_process');
const host = require('./host');

// Windows では CLI は WSL に居るので wsl.exe -e bash -lc に載せる（cwd も WSL 表記へ）。
// Linux / macOS はそのまま起動する。
function spawnSpec(command, args, { cwd = '', env = {}, distro = '' } = {}) {
  if (process.platform !== 'win32') return { command, args, extra: { cwd, env: { ...process.env, ...env }, detached: true } };
  const wsl = host.wslArgv(command, args, { cwd, env, distro });
  return { command: wsl.command, args: wsl.args, extra: { windowsHide: true } };
}

function capture(argv, cwd) {
  return new Promise((resolve) => {
    execFile(argv[0], argv.slice(1), { cwd, windowsHide: true, timeout: 30000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => resolve(err && !stdout ? '' : String(stdout || '')));
  });
}

function killTree(child) {
  try {
    if (process.platform === 'win32') execFile('taskkill', ['/pid', String(child.pid), '/T', '/F'], () => {});
    else process.kill(-child.pid, 'SIGTERM');
  } catch {
    try { child.kill(); } catch { /* 既に終わっている */ }
  }
}

module.exports = { spawnSpec, capture, killTree };
