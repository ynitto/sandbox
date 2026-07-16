'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { makeLoopProvider, isWslPath, wslPath, shellQuote, sh: providerSh } = require('./loopProvider');
const { _pathKey } = require('../../agent-project/main/project');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');
const {
  discoverCoworkItems, parseKiroLoopPrompts, scheduleOf,
} = require('./discover');
const { applyKiroLoopEdits, applyStatemachineEdits } = require('./writeback');

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
    source: 'config',
    state: dynamicState({ ...item, id, type }, cfg),
  };
}

// 発見項目（discover.js 由来）を Cowork 項目へ。source/_src/enabled を保持しつつ live state を付与。
function normalizeDiscovered(d, cfg) {
  const type = d.type === 'state-machine' ? 'state-machine' : 'loop';
  return {
    id: d.id,
    type,
    name: String(d.name || d.id),
    repo: d.repo || '',
    branch: '',
    schedule: d.schedule || '',
    workflow: d.workflow || '',
    description: d.description || '',
    command: type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'kiro-loop'),
    source: 'discovered',
    enabled: d.enabled !== false,
    _src: d._src,
    state: dynamicState({ ...d, type }, cfg),
  };
}

// 重複排除キー: type|repo実体|ジョブ名。先に並ぶ config 項目が発見項目に勝つ（手動登録が正）。
function jobKey(it) {
  const name = (it._src && (it._src.promptName || it._src.workflowName)) || it.name || '';
  return `${it.type}|${_pathKey(it.repo || '')}|${name}`;
}

function dedupeItems(items) {
  const seen = new Set();
  const out = [];
  for (const it of items) {
    const k = jobKey(it);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(it);
  }
  return out;
}

function discoverNormalized(config, cfg) {
  try {
    return discoverCoworkItems(config).map((d) => normalizeDiscovered(d, cfg));
  } catch {
    return [];   // 発見の失敗で overview 全体を壊さない
  }
}

function overview(config) {
  const cfg = config.cowork || {};
  const loop = makeLoopProvider(cfg);
  const configItems = itemsOf(cfg).map((item, i) => normalizeItem(item, i, cfg));
  const discovered = discoverNormalized(config, cfg);
  const items = dedupeItems([...configItems, ...discovered]);
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

// 手動登録（cfg.cowork.items）→ 無ければ発見項目から id 一致で解決する。
function resolveItem(config, id) {
  const cfg = config.cowork || {};
  const inCfg = itemsOf(cfg).find((item, i) => itemId(item, i) === String(id));
  if (inCfg) return { ...inCfg, source: 'config' };
  try {
    return discoverCoworkItems(config).find((d) => d.id === String(id)) || null;
  } catch {
    return null;
  }
}

function runLoop(config, itemIdValue) {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  // 発見 loop の実行対象は kiro-loop の prompt 名（合成 id ではない）。
  const runId = item.source === 'discovered'
    ? ((item._src && item._src.promptName) || item.name)
    : (item.id || item.name);
  return makeLoopProvider(cfg).run({ ...item, cwd: item.repo || item.cwd, id: runId });
}

function runStateMachine(config, itemIdValue, input) {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
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

// 指定ファイル（repo 相対 POSIX パス）に差分があればそれだけを commit する。無ければ skip。
function gitCommitFiles(repo, relFiles, message) {
  if (!relFiles.length) return { ok: true, skipped: true };
  const st = gitInRepo(repo, ['status', '--porcelain', '--', ...relFiles], 10000);
  if (!st.ok) return { ok: false, step: 'status', error: st.stderr || st.error };
  if (!st.stdout.trim()) return { ok: true, skipped: true };
  const add = gitInRepo(repo, ['add', '--', ...relFiles], 30000);
  if (!add.ok) return { ok: false, step: 'add', error: add.stderr || add.error };
  const ci = gitInRepo(repo, ['commit', '-m', message, '--', ...relFiles], 30000);
  if (!ci.ok) return { ok: false, step: 'commit', error: ci.stderr || ci.error };
  return { ok: true };
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

// repo からの相対 POSIX パス（WSL の git でも -C の git でも解決できる形）。
function relPosix(repo, file) {
  return path.relative(repo, file).split(path.sep).join('/');
}

// JSON 形式の kiro-loop 設定へ発見項目の編集を反映（parse → mutate → stringify）。
function applyKiroLoopJson(raw, items) {
  let obj;
  try { obj = JSON.parse(raw); } catch { return { text: raw, changed: false, errors: ['kiro-loop.json の解析に失敗'] }; }
  const prompts = Array.isArray(obj && obj.prompts) ? obj.prompts : [];
  let changed = false;
  for (const it of items) {
    const p = prompts[it._src.promptIndex];
    if (!p || typeof p !== 'object') continue;
    if ((it.name || '') !== (p.name || '')) { p.name = it.name || ''; changed = true; }
    const curEnabled = p.enabled !== false;
    if ((it.enabled !== false) !== curEnabled) { p.enabled = it.enabled !== false; changed = true; }
    if (it._src.scheduleKey === 'cron') {
      if ((it.schedule || '') !== String(p.cron || '')) { p.cron = it.schedule || ''; changed = true; }
    } else if (it._src.scheduleKey === 'interval_minutes') {
      const n = parseInt(String(it.schedule || '').replace(/m$/i, ''), 10);
      if (!Number.isNaN(n) && n !== p.interval_minutes) { p.interval_minutes = n; changed = true; }
    }
  }
  return { text: changed ? `${JSON.stringify(obj, null, 2)}\n` : raw, changed, errors: [] };
}

// 発見項目の編集を _src.file 単位に束ねて実体へ書き戻す。差分がある時だけ write。
// 返り値 { touched: [{repo, relFiles:[...]}], errors:[...] }。
function applyDiscoveredEdits(discovered) {
  const byFile = new Map();
  for (const it of discovered) {
    const f = it._src.file;
    if (!byFile.has(f)) byFile.set(f, []);
    byFile.get(f).push(it);
  }
  const touched = [];
  const errors = [];
  for (const [file, items] of byFile) {
    const first = items[0]._src;
    let raw;
    try { raw = fs.readFileSync(file, 'utf8'); } catch { errors.push(`読み込み失敗: ${file}`); continue; }
    let newText = null;

    if (first.kind === 'kiro-loop') {
      if (first.format === 'json') {
        const r = applyKiroLoopJson(raw, items);
        errors.push(...r.errors);
        if (r.changed) newText = r.text;
      } else {
        const current = parseKiroLoopPrompts(raw.replace(/\r\n/g, '\n'));
        const edits = [];
        for (const it of items) {
          const cur = current[it._src.promptIndex] || {};
          const edit = { promptIndex: it._src.promptIndex, promptName: it._src.promptName, scheduleKey: it._src.scheduleKey };
          let changed = false;
          if ((it.name || '') !== (cur.name || '')) { edit.name = it.name || ''; changed = true; }
          if ((it.enabled !== false) !== (cur.enabled !== false)) { edit.enabled = it.enabled !== false; changed = true; }
          if (it._src.scheduleKey && (it.schedule || '') !== scheduleOf(cur).schedule) {
            edit.schedule = it.schedule || '';
            changed = true;
          }
          if (changed) edits.push(edit);
        }
        if (edits.length) {
          const r = applyKiroLoopEdits(raw, edits);
          errors.push(...r.errors);
          newText = r.text;
        }
      }
    } else if (first.kind === 'statemachine') {
      const it = items[0];
      const meta = parseFlatYaml(raw.replace(/\r\n/g, '\n'));
      const curName = meta.name || it._src.workflowName;
      const curDesc = meta.description || '';
      const edits = {};
      let changed = false;
      if ((it.name || '') !== curName) { edits.name = it.name || ''; changed = true; }
      if ((it.description || '') !== curDesc) { edits.description = it.description || ''; changed = true; }
      if (changed) {
        const r = applyStatemachineEdits(raw, edits);
        errors.push(...r.errors);
        newText = r.text;
      }
    }

    if (newText != null && newText !== raw) {
      try {
        fs.writeFileSync(file, newText, 'utf8');
        touched.push({ repo: first.repo, relFiles: [relPosix(first.repo, file)] });
      } catch {
        errors.push(`書き込み失敗: ${file}`);
      }
    }
  }
  return { touched, errors };
}

// 手動項目を config へ保存する際、実行時フィールドを落とす。
function stripRuntimeFields(it) {
  const { state, _src, source, enabled, command, ...rest } = it;
  return rest;
}

function saveWork(config, saveConfig, { items, branch, createBranch, push } = {}) {
  const all = Array.isArray(items) ? items : [];
  const configItems = all.filter((it) => it.source !== 'discovered');
  const discovered = all.filter((it) => it.source === 'discovered' && it._src);

  // 1) 手動項目のみ dashboard 設定へ保存（発見項目は実体ファイルが正）
  const cfg = { ...(config || {}) };
  cfg.cowork = { ...(cfg.cowork || {}), items: configItems.map(stripRuntimeFields) };
  const saved = saveConfig(cfg);

  // 2) 発見項目の編集を実体ファイルへ書き戻し
  const wb = applyDiscoveredEdits(discovered);

  // 3) touched repo（書き戻し先）＋手動項目の repo を commit → branch/create/push
  const repoMap = new Map(); // repoKey -> { repo, relFiles:Set }
  const ensure = (repo) => {
    if (!repo) return null;
    const k = _pathKey(repo);
    if (!repoMap.has(k)) repoMap.set(k, { repo, relFiles: new Set() });
    return repoMap.get(k);
  };
  for (const t of wb.touched) {
    const e = ensure(t.repo);
    if (e) t.relFiles.forEach((f) => e.relFiles.add(f));
  }
  for (const it of configItems) ensure(it.repo);

  const git = [...repoMap.values()].map(({ repo, relFiles }) => {
    const files = [...relFiles];
    const commit = gitCommitFiles(repo, files, 'chore(cowork): update kiro-loop/statemachine config');
    const save = gitSave(repo, { branch, createBranch, push });
    return { repo, result: { ...save, commit } };
  });
  return { config: saved, git, writeback: { errors: wb.errors } };
}

module.exports = {
  overview, runLoop, runStateMachine, saveWork, itemsOf, wslPath, dynamicState,
  resolveItem, findItem, dedupeItems, applyDiscoveredEdits, gitCommitFiles,
};
