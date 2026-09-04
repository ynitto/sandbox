'use strict';

const { spawnSync } = require('child_process');

function shellQuote(s) {
  return `'${String(s).replace(/'/g, `'"'"'`)}'`;
}

// パス表記の変換は base/main/wsl.js が正典（cowork・定常業務・documents で共有する）。
const { isWslPath, wslPath, wslDistro, winDriveToWsl, toWslPath } = require('../../../base/main/wsl');

// repo（WSL UNC / POSIX / Windows ドライブ）を WSL 側の Linux パスへ寄せる。
// Windows ドライブ上のリポジトリでも agent-loop のペイン cwd（/mnt/c/...）と照合できる。
const toWslCwd = toWslPath;

function decodeCliOutput(buf) {
  if (buf == null) return '';
  if (typeof buf === 'string') return buf;
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
  if (!b.length) return '';
  const utf8 = b.toString('utf8');
  if (!utf8.includes('\uFFFD')) return utf8;
  try {
    return new TextDecoder('shift_jis').decode(b);
  } catch {
    return utf8;
  }
}

function resultOf(res) {
  return {
    ok: res.status === 0,
    status: res.status,
    stdout: decodeCliOutput(res.stdout).trimEnd(),
    stderr: decodeCliOutput(res.stderr).trimEnd(),
    error: res.error ? res.error.message : '',
  };
}

// Windows では常に WSL へ。Linux ネイティブではそのまま tmux を叩く。
// ログインシェルは bash を使う（sh=dash だと利用者の ~/.bashrc / profile にある bash 構文
// `[[ … ]]` が `sh: N: [[: not found` になり、そこで止まると venv 有効化も走らず、
// agent-loop（`#!/usr/bin/env python`）の python も解決できず「python: No such file or directory」になる）。
function shInWsl(script, timeoutMs = 8000, distro = '') {
  const wrapped = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${script}`;
  if (process.platform === 'win32') {
    const wslArgs = distro ? ['-d', distro, '-e', 'bash', '-lc', wrapped] : ['-e', 'bash', '-lc', wrapped];
    return resultOf(spawnSync('wsl.exe', wslArgs, {
      encoding: 'buffer',
      timeout: timeoutMs,
      windowsHide: true,
    }));
  }
  return resultOf(spawnSync('bash', ['-lc', wrapped], {
    encoding: 'buffer',
    timeout: timeoutMs,
    windowsHide: true,
  }));
}

module.exports = {
  shellQuote, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, decodeCliOutput, shInWsl,
};

