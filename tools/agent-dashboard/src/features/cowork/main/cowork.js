'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { makeLoopProvider, isWslPath, wslPath, shellQuote, sh: providerSh } = require('./loopProvider');

function sh(command, args, options = {}) {
  const res = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    encoding: options.encoding || 'utf8',
    shell: process.platform === 'win32' && command !== 'git',
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

function itemsOf(cfg) {
  return Array.isArray(cfg.items) ? cfg.items : [];
}

function itemId(item, i) {
  return String(item.id || item.name || `${item.type || 'work'}-${i + 1}`);
}

function listLogCandidates(repo, type) {
  const names = type === 'loop'
    ? ['.kiro-loop/logs', '.agent-loop/logs', 'logs']
    : ['.statemachine-use/logs', 'logs'];
  const out = [];
  for (const n of names) {
    const dir = path.join(repo, n);
    try {
      for (const f of fs.readdirSync(dir)) {
        if (/\.(log|jsonl|txt)$/i.test(f) || f.includes('kiro') || f.includes('agent-loop') || f.includes('statemachine')) {
          const file = path.join(dir, f);
          const st = fs.statSync(file);
          if (st.isFile()) out.push({ file, mtimeMs: st.mtimeMs });
        }
      }
    } catch { /* optional logs */ }
  }
  return out.sort((a, b) => b.mtimeMs - a.mtimeMs);
}

function tail(file, max = 1200) {
  try {
    const s = fs.readFileSync(file, 'utf8');
    return s.slice(-max);
  } catch { return ''; }
}

function processStatus(item, cfg) {
  const repo = item.repo || item.cwd || '';
  const needle = repo ? wslPath(repo) : itemId(item, 0);
  const command = item.type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'kiro-loop');
  if (process.platform === 'win32' && isWslPath(repo)) {
    const script = `pgrep -af ${shellQuote(command)} | grep -F -- ${shellQuote(needle)} | grep -v grep | head -1`;
    const r = sh('wsl.exe', ['-e', 'sh', '-lc', script], { timeoutMs: 8000 });
    return r.ok && r.stdout ? { running: true, detail: r.stdout } : { running: false, detail: '' };
  }
  const r = sh(process.platform === 'win32' ? 'wmic' : 'sh', process.platform === 'win32'
    ? ['process', 'where', `CommandLine like '%${command}%'`, 'get', 'ProcessId,CommandLine']
    : ['-lc', `pgrep -af ${shellQuote(command)} | grep -F -- ${shellQuote(needle)} | grep -v grep | head -1`], { timeoutMs: 8000 });
  return r.ok && r.stdout && r.stdout.includes(command) ? { running: true, detail: r.stdout } : { running: false, detail: '' };
}

function dynamicState(item, cfg) {
  const repo = item.repo || item.cwd || '';
  const proc = processStatus(item, cfg);
  const logs = repo ? listLogCandidates(repo, item.type) : [];
  const latest = logs[0] || null;
  const text = latest ? tail(latest.file) : '';
  let status = proc.running ? 'running' : latest ? 'idle' : 'unknown';
  if (/\b(error|failed|exception|traceback)\b/i.test(text)) status = proc.running ? 'running' : 'failed';
  if (/\b(done|complete|success|finished)\b/i.test(text) && !proc.running) status = 'done';
  return {
    status,
    running: proc.running,
    process: proc.detail,
    lastLog: latest ? latest.file : '',
    lastLogAt: latest ? new Date(latest.mtimeMs).toISOString() : '',
    logTail: text,
  };
}

function normalizeItem(item, i, cfg) {
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const id = itemId({ ...item, type }, i);
  return {
    id,
    type,
    name: String(item.name || item.id || (type === 'loop' ? `定期実行 ${i + 1}` : `定型業務 ${i + 1}`)),
    repo: item.repo || item.cwd || '',
    branch: item.branch || '',
    schedule: item.schedule || item.cron || '',
    workflow: item.workflow || item.file || '',
    description: item.description || '',
    command: type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'kiro-loop'),
    state: dynamicState({ ...item, id, type }, cfg),
  };
}

function overview(config) {
  const cfg = config.cowork || {};
  const loop = makeLoopProvider(cfg);
  const items = itemsOf(cfg).map((item, i) => normalizeItem(item, i, cfg));
  return {
    loopProvider: loop.provider,
    loopCommand: loop.command,
    replacementHint: loop.replacementHint,
    stateMachineCommand: cfg.stateMachineCommand || 'statemachine-use',
    items,
  };
}

function findItem(cfg, id) {
  return itemsOf(cfg).find((item, i) => itemId(item, i) === String(id));
}

function runLoop(config, itemIdValue) {
  const cfg = config.cowork || {};
  const item = findItem(cfg, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  return makeLoopProvider(cfg).run({ ...item, cwd: item.repo || item.cwd, id: item.id || item.name });
}

function runStateMachine(config, itemIdValue, input) {
  const cfg = config.cowork || {};
  const item = findItem(cfg, itemIdValue);
  if (!item) throw new Error(`Cowork 定型業務が見つかりません: ${itemIdValue}`);
  const args = Array.isArray(item.args) ? [...item.args] : ['run', item.workflow || item.file].filter(Boolean);
  if (input) args.push(String(input));
  return providerSh(cfg.stateMachineCommand || 'statemachine-use', args, { cwd: item.repo || item.cwd || process.cwd(), timeoutMs: item.timeoutMs || 60000 });
}

function gitInRepo(repo, args, timeoutMs) {
  if (process.platform === 'win32' && isWslPath(repo)) {
    const script = `cd ${shellQuote(wslPath(repo))} && git ${args.map(shellQuote).join(' ')}`;
    return sh('wsl.exe', ['-e', 'sh', '-lc', script], { timeoutMs });
  }
  return sh('git', ['-C', repo, ...args], { timeoutMs });
}

function gitSave(repo, { branch, createBranch, push } = {}) {
  if (!repo) return { skipped: true };
  const current = gitInRepo(repo, ['rev-parse', '--abbrev-ref', 'HEAD'], 10000);
  if (!current.ok) return { ok: false, error: current.stderr || current.error || 'git rev-parse failed' };
  let checkout = null;
  if (branch) {
    checkout = gitInRepo(repo, createBranch ? ['checkout', '-B', branch] : ['switch', branch], 30000);
    if (!checkout.ok) return { ok: false, step: 'checkout', error: checkout.stderr || checkout.error };
  }
  let pushed = null;
  if (push) {
    const b = branch || current.stdout;
    pushed = gitInRepo(repo, ['push', '-u', 'origin', b], 120000);
    if (!pushed.ok) return { ok: false, step: 'push', error: pushed.stderr || pushed.error };
  }
  return { ok: true, branch: branch || current.stdout, checkout, pushed };
}

function saveWork(config, saveConfig, { items, branch, createBranch, push } = {}) {
  const cfg = { ...(config || {}) };
  cfg.cowork = { ...(cfg.cowork || {}), items: Array.isArray(items) ? items : [] };
  const saved = saveConfig(cfg);
  const repos = [...new Set((cfg.cowork.items || []).map((x) => x.repo).filter(Boolean))];
  const git = repos.map((repo) => ({ repo, result: gitSave(repo, { branch, createBranch, push }) }));
  return { config: saved, git };
}

module.exports = { overview, runLoop, runStateMachine, saveWork, itemsOf, wslPath, dynamicState };
