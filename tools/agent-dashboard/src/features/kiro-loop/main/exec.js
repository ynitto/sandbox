'use strict';

const { spawnSync } = require('child_process');

function shellQuote(s) {
  return `'${String(s).replace(/'/g, `'"'"'`)}'`;
}

function isWslPath(p) {
  const s = String(p || '');
  return /^\\\\wsl(?:\$|\.localhost)\\/i.test(s) || /^\//.test(s);
}

function wslPath(p) {
  const s = String(p || '');
  const unc = s.replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (unc) return (unc[1] || '').replace(/\\/g, '/') || '/';
  return s;
}

function wslDistro(p) {
  const s = String(p || '');
  const unc = s.replace(/\//g, '\\').match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  return unc ? unc[1] : '';
}

// Windows ドライブパス（C:\foo\bar）→ WSL の /mnt/c/foo/bar。該当しなければ ''。
function winDriveToWsl(p) {
  const m = String(p || '').replace(/\//g, '\\').match(/^([A-Za-z]):(\\.*)?$/);
  if (!m) return '';
  const rest = (m[2] || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return `/mnt/${m[1].toLowerCase()}${rest}`;
}

// repo（WSL UNC / POSIX / Windows ドライブ）を WSL 側の Linux パスへ寄せる。
// Windows ドライブ上のリポジトリでも kiro-loop のペイン cwd（/mnt/c/...）と照合できる。
function toWslCwd(p) {
  if (isWslPath(p)) return wslPath(p);
  return winDriveToWsl(p) || String(p || '');
}

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
function shInWsl(script, timeoutMs = 8000, distro = '') {
  const wrapped = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; ${script}`;
  if (process.platform === 'win32') {
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', wrapped] : ['-e', 'sh', '-lc', wrapped];
    return resultOf(spawnSync('wsl.exe', wslArgs, {
      encoding: 'buffer',
      timeout: timeoutMs,
      windowsHide: true,
    }));
  }
  return resultOf(spawnSync('sh', ['-lc', wrapped], {
    encoding: 'buffer',
    timeout: timeoutMs,
    windowsHide: true,
  }));
}

module.exports = {
  shellQuote, isWslPath, wslPath, wslDistro, winDriveToWsl, toWslCwd, decodeCliOutput, shInWsl,
};
