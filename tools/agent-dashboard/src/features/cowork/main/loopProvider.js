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

function sh(command, args, options = {}) {
  if (process.platform === 'win32' && isWslPath(options.cwd)) {
    const cwd = wslPath(options.cwd);
    const script = `cd ${shellQuote(cwd)} && ${shellQuote(command)} ${args.map(shellQuote).join(' ')}`;
    const res = spawnSync('wsl.exe', ['-e', 'sh', '-lc', script], {
      encoding: 'utf8',
      timeout: options.timeoutMs || 30000,
      windowsHide: true,
    });
    return {
      ok: res.status === 0,
      status: res.status,
      stdout: (res.stdout || '').trim(),
      stderr: (res.stderr || '').trim(),
      error: res.error ? res.error.message : '',
    };
  }
  const res = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    encoding: 'utf8',
    shell: process.platform === 'win32',
    timeout: options.timeoutMs || 30000,
    windowsHide: true,
  });
  return {
    ok: res.status === 0,
    status: res.status,
    stdout: (res.stdout || '').trim(),
    stderr: (res.stderr || '').trim(),
    error: res.error ? res.error.message : '',
  };
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

module.exports = { makeLoopProvider, isWslPath, wslPath, shellQuote, sh };
