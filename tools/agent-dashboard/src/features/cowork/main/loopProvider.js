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

// Windows ネイティブ CLI は CP932、WSL は UTF-8。encoding:'utf8' 固定だと日本語が文字化けする。
// buffer で受け取り、UTF-8 → だめなら Shift_JIS（CP932 系）へフォールバックする。
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
    stdout: decodeCliOutput(res.stdout).trim(),
    stderr: decodeCliOutput(res.stderr).trim(),
    error: res.error ? res.error.message : '',
  };
}

function sh(command, args, options = {}) {
  const argv = (args || []).map(String);
  if (process.platform === 'win32' && isWslPath(options.cwd)) {
    const cwd = wslPath(options.cwd);
    const distro = wslDistro(options.cwd);
    // LANG を明示しないと WSL 側のロケールで日本語 stderr が化けることがある。
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; cd ${shellQuote(cwd)} && ${shellQuote(command)} ${argv.map(shellQuote).join(' ')}`;
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script];
    const res = spawnSync('wsl.exe', wslArgs, {
      encoding: 'buffer',
      timeout: options.timeoutMs || 30000,
      windowsHide: true,
    });
    return resultOf(res);
  }
  // shell:true は cmd.exe 経由で日本語引数・出力を壊す（agent-project/actions.js と同方針）。
  const res = spawnSync(String(command), argv, {
    cwd: options.cwd || process.cwd(),
    encoding: 'buffer',
    shell: false,
    timeout: options.timeoutMs || 30000,
    windowsHide: true,
  });
  return resultOf(res);
}

function makeLoopProvider(cfg) {
  const provider = cfg.loopProvider || 'kiro-loop';
  const command = cfg.loopCommand || provider;
  return {
    provider,
    command,
    replacementHint: cfg.nextLoopProvider || 'agent-loop',
    run(job) {
      const args = Array.isArray(job.args) ? job.args : ['run', job.id || job.name].filter(Boolean);
      return sh(command, args, { cwd: job.cwd || job.repo, timeoutMs: job.timeoutMs || 60000 });
    },
  };
}

module.exports = { makeLoopProvider, isWslPath, wslPath, wslDistro, shellQuote, sh, decodeCliOutput };
