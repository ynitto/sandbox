'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const {
  makeLoopProvider, runChatWindow, isWslPath, wslPath, wslDistro, toWslCwd, shellQuote, decodeCliOutput,
  runCommandWindow, runCommandCapture, supportsRunWindow,
} = require('./loopProvider');
const { _pathKey, _isPosixAbs, toViewerPath, viewerDistro } = require('../../agent-project/main/project');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');
const {
  discoverCoworkItems, parseAgentLoopPrompts, scheduleOf, detectMarkers, agentLoopPromptTexts,
  scanForCoworkConfigs, isDir, coworkRoots, loopConfigFile,
} = require('./discover');
const { applyAgentLoopEdits, applyStatemachineEdits, upsertManagedAgentPrompt } = require('./writeback');
const globalInstructions = require('../../orchestration/main/instructions');
const sessionCommands = require('../../orchestration/main/sessionCommands');
const profiles = require('../../orchestration/main/profiles');
const agentCli = require('../../agent-project/main/agentCli');

// 設定に書かれたフォルダ表記を、このビュアーで開けるパスへ揃える（discover と同じ規則）。
// WSL の Linux 絶対パスは Windows ビュアーでは UNC へ翻訳する（そうしないと C:\home\… に化ける）。
function _resolveRoot(raw, config) {
  const s = String(raw || '').replace(/^~(?=$|\/|\\)/, os.homedir());
  if (!s) return '';
  return _isPosixAbs(s) ? toViewerPath(s, viewerDistro(config)) : path.resolve(s);
}

// 発見結果キャッシュ。overview のポーリングごとに roots を再走査しない。
const DISCOVER_TTL_MS = 30000;
let _discoverCache = { key: '', at: 0, items: null };

function discoverCacheKey(config) {
  // 走査ルートは discover.coworkRoots と同じもの（エンジン担当 + cowork.roots）を使う。
  // engine 側だけを鍵にすると、フォルダを登録／解除しても鍵が変わらず、TTL が切れるまで
  // 古い発見結果を返し続ける。
  const roots = coworkRoots(config).map(String).join('\0');
  const cw = (config && config.cowork) || {};
  const depth = cw.scanDepth || 2;
  return `${roots}|${depth}|${cw.discover === false ? '0' : '1'}`;
}

function invalidateDiscoverCache() {
  _discoverCache = { key: '', at: 0, items: null };
}

function sh(command, args, options = {}) {
  const argv = (args || []).map(String);
  const res = spawnSync(String(command), argv, {
    cwd: options.cwd || process.cwd(),
    encoding: 'buffer',
    // git / wsl.exe は argv 配列で直接起動（cmd.exe 経由の日本語化けを避ける）
    shell: false,
    timeout: options.timeoutMs || 30000,
    windowsHide: true,
  });
  return {
    ok: res.status === 0,
    status: res.status,
    stdout: decodeCliOutput(res.stdout).trim(),
    stderr: decodeCliOutput(res.stderr).trim(),
    error: res.error ? res.error.message : '',
  };
}

function itemsOf(cfg) {
  return Array.isArray(cfg.items) ? cfg.items : [];
}

function itemId(item, i) {
  return String(item.id || item.name || `${item.type || 'work'}-${i + 1}`);
}

// Windows dashboard から POSIX リポジトリを読むときは UNC へ（discover と同じ橋渡し）。
// **config を必ず渡す**——⚙ 設定のディストロを使わずに既定へ丸めると、engine / nodeRepos
// 経由の経路（プロジェクト・CLIチャット・対話診断）とは違うディストロを指し、
// 「相談ボタンからは開けるのに定常業務からは開けない」という食い違いになる。
function viewerRepo(repo, config) {
  const s = String(repo || '');
  if (!s) return '';
  if (process.platform === 'win32' && _isPosixAbs(s)) return toViewerPath(s, viewerDistro(config));
  return s;
}

function listLogCandidates(repo, type, config) {
  const root = viewerRepo(repo, config);
  if (!root) return [];
  const names = type === 'loop'
    ? ['.agent-loop/logs', 'logs']
    : ['.statemachine-use/logs', 'logs'];
  const out = [];
  for (const n of names) {
    const dir = path.join(root, n);
    try {
      for (const f of fs.readdirSync(dir)) {
        if (/\.(log|jsonl|txt)$/i.test(f) || f.includes('kiro') || f.includes('agent-loop') || f.includes('statemachine')) {
          const file = path.join(dir, f);
          const st = fs.statSync(file);
          if (st.isFile()) out.push({ file, mtimeMs: st.mtimeMs, size: st.size });
        }
      }
    } catch { /* optional logs */ }
  }
  return out.sort((a, b) => b.mtimeMs - a.mtimeMs);
}

// 同じリポジトリ・同じ種類のログを、定常業務ごとに分離する。
//
// 旧実装は repo と type だけで候補を集めていたため、同じ repo に loop が 2 件あると
// 2 件とも「repo 内の最新ログ」を自分の状態として使い、履歴にも全ログを並べていた。
// ログの保存形式は世代ごとに異なるので、ファイル名を第一候補、ログ内の prompt 名 / workflow
// 名を第二候補として、同居する業務のうち一意に特定できたときだけ所属させる。識別不能な
// ログを全業務へ混ぜるより、表示しない方が正しい。業務が 1 件だけなら従来ログも曖昧でない。
const LOG_OWNER_CACHE_LIMIT = 1000;
const _logOwnerCache = new Map();

function compactLogIdentity(value) {
  return String(value == null ? '' : value)
    .normalize('NFKC')
    .toLocaleLowerCase('ja-JP')
    .replace(/[^\p{L}\p{N}]+/gu, '');
}

function itemLogIdentities(item) {
  const src = (item && item._src) || {};
  const values = [
    item && !String(item.id || '').startsWith('disc:') ? item.id : '',
    item && item.name,
    item && item.workflow,
    item && item.file,
    src.promptName,
    src.workflowName,
    src.loop && src.loop.promptName,
  ];
  const out = new Set();
  for (const value of values) {
    const raw = String(value || '');
    if (!raw) continue;
    const base = path.basename(raw).replace(/\.(?:ya?ml|json)$/i, '');
    for (const token of [compactLogIdentity(raw), compactLogIdentity(base)]) {
      if (token.length >= 2) out.add(token);
    }
  }
  return [...out];
}

function peerLogItems(config, item) {
  if (!config) return [item];
  const cfg = config.cowork || {};
  const configured = itemsOf(cfg).map((candidate, index) => ({
    ...candidate,
    id: itemId(candidate, index),
    source: 'config',
  }));
  let discovered = [];
  try { discovered = rawDiscovered(config); } catch { /* 発見失敗時は設定項目だけで判定 */ }
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const repoKey = _pathKey(item.repo || item.cwd || '');
  return dedupeItems([...configured, ...discovered, item]).filter((candidate) => (
    (candidate.type === 'state-machine' ? 'state-machine' : 'loop') === type
    && _pathKey(candidate.repo || candidate.cwd || '') === repoKey
  ));
}

function logIdentitySample(candidate, maxBytes = 65536) {
  let fd;
  try {
    const size = Number(candidate.size) || fs.statSync(candidate.file).size;
    const chunkSize = Math.min(Math.ceil(maxBytes / 2), size);
    if (!chunkSize) return '';
    fd = fs.openSync(candidate.file, 'r');
    const first = Buffer.alloc(chunkSize);
    const firstRead = fs.readSync(fd, first, 0, chunkSize, 0);
    if (size <= chunkSize) return first.subarray(0, firstRead).toString('utf8');
    const last = Buffer.alloc(Math.min(chunkSize, size - chunkSize));
    const lastRead = fs.readSync(fd, last, 0, last.length, size - last.length);
    return `${first.subarray(0, firstRead).toString('utf8')}\n${last.subarray(0, lastRead).toString('utf8')}`;
  } catch {
    return '';
  } finally {
    if (fd !== undefined) {
      try { fs.closeSync(fd); } catch { /* 読み取り失敗後の close は無視 */ }
    }
  }
}

function highestUniqueOwner(peers, score) {
  let best = 0;
  let owner = '';
  let tied = false;
  for (const peer of peers) {
    const value = score(peer);
    if (value > best) {
      best = value;
      owner = jobKey(peer);
      tied = false;
    } else if (value > 0 && value === best && jobKey(peer) !== owner) {
      tied = true;
    }
  }
  return best > 0 && !tied ? owner : '';
}

function logCandidateOwner(candidate, peers) {
  const peerSignature = peers.map((peer) => `${jobKey(peer)}:${itemLogIdentities(peer).join(',')}`)
    .sort().join('|');
  const cacheKey = `${candidate.file}\0${candidate.mtimeMs}\0${candidate.size}\0${peerSignature}`;
  if (_logOwnerCache.has(cacheKey)) return _logOwnerCache.get(cacheKey);

  const basename = compactLogIdentity(path.basename(candidate.file, path.extname(candidate.file)));
  let owner = highestUniqueOwner(peers, (peer) => {
    let best = 0;
    for (const token of itemLogIdentities(peer)) {
      if (basename === token) best = Math.max(best, 2000 + token.length);
      else if (basename.includes(token)) best = Math.max(best, 1000 + token.length);
    }
    return best;
  });
  if (!owner) {
    const sample = compactLogIdentity(logIdentitySample(candidate));
    owner = highestUniqueOwner(peers, (peer) => {
      let best = 0;
      for (const token of itemLogIdentities(peer)) {
        if (sample.includes(token)) best = Math.max(best, 100 + token.length);
      }
      return best;
    });
  }

  if (_logOwnerCache.size >= LOG_OWNER_CACHE_LIMIT) {
    _logOwnerCache.delete(_logOwnerCache.keys().next().value);
  }
  _logOwnerCache.set(cacheKey, owner);
  return owner;
}

function listItemLogCandidates(item, config) {
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const candidates = listLogCandidates(item.repo || item.cwd || '', type, config);
  const peers = peerLogItems(config, { ...item, type });
  if (peers.length <= 1) return candidates;
  const target = jobKey({ ...item, type });
  return candidates.filter((candidate) => logCandidateOwner(candidate, peers) === target);
}

function tail(file, max = 1200) {
  try {
    const s = fs.readFileSync(file, 'utf8');
    return s.slice(-max);
  } catch { return ''; }
}

// ---------------------------------------------------------------------------
// 実行履歴（dashboard から実行した記録）。ビュアー固有のデータなのでリポジトリには
// 書かず、ホーム配下の JSONL に追記する。項目のキーは jobKey（type|repo|名前）＝
// 発見/手動や id 形式の変更を跨いで安定。
// ---------------------------------------------------------------------------
const HISTORY_KEEP = 500;      // 溢れたら新しい方からこの件数まで切り詰める
const HISTORY_TRIM_AT = 1000;

function historyFile(cfg) {
  return String((cfg && cfg.historyFile) || path.join(os.homedir(), '.agent-dashboard', 'cowork-history.jsonl'));
}

function readHistoryLines(file) {
  try {
    return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean);
  } catch { return []; }
}

function appendHistory(cfg, record) {
  const file = historyFile(cfg);
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`, 'utf8');
    const lines = readHistoryLines(file);
    if (lines.length > HISTORY_TRIM_AT) {
      fs.writeFileSync(file, `${lines.slice(-HISTORY_KEEP).join('\n')}\n`, 'utf8');
    }
  } catch { /* 履歴の書き込み失敗で実行自体は壊さない */ }
}

function readHistory(cfg, key, limit = 50) {
  const out = [];
  for (const line of readHistoryLines(historyFile(cfg))) {
    try {
      const rec = JSON.parse(line);
      if (rec && rec.key === key) out.push(rec);
    } catch { /* 壊れた行はスキップ */ }
  }
  return out.slice(-Math.max(1, limit)).reverse();   // 新しい順
}

function recordRun(cfg, item, res) {
  appendHistory(cfg, {
    at: new Date().toISOString(),
    key: jobKey(item),
    id: String(item.id || ''),
    name: String(item.name || item.id || ''),
    type: item.type === 'state-machine' ? 'state-machine' : 'loop',
    repo: String(item.repo || item.cwd || ''),
    ok: !!(res && res.ok),
    // 別ウィンドウ実行（launched）は stdout/stderr を持たないため message を残す
    message: String((res && (res.stderr || res.stdout || res.error || res.message)) || '').trim().slice(0, 300),
  });
}

// 項目の実行履歴＋リポジトリのログ候補一覧。ログ本文は readLog で個別に取る。
function itemLogs(config, itemIdValue, { historyLimit = 50 } = {}) {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const repo = item.repo || item.cwd || '';
  const logs = listItemLogCandidates({ ...item, type }, config).map((l) => ({
    file: l.file,
    name: path.basename(l.file),
    mtimeMs: l.mtimeMs,
    size: l.size,
  }));
  return {
    id: String(item.id || itemIdValue),
    name: String(item.name || item.id || ''),
    type,
    repo,
    history: readHistory(cfg, jobKey({ ...item, type }), historyLimit),
    logs,
  };
}

// ログ本文（末尾）。パスの正当性は「その項目のログ候補に実在するか」で検証する
// （renderer から任意パスを読ませない）。
function readLog(config, itemIdValue, file, maxBytes = 16000) {
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const candidates = listItemLogCandidates({ ...item, type }, config);
  const hit = candidates.find((l) => l.file === String(file || ''));
  if (!hit) throw new Error('この作業のログではありません');
  const limit = Math.max(1000, Math.min(Number(maxBytes) || 16000, 200000));
  let size = 0;
  try { size = fs.statSync(hit.file).size; } catch { /* stat 失敗は 0 扱い */ }
  return {
    file: hit.file,
    size,
    truncated: size > limit,
    text: tail(hit.file, limit),
  };
}

function processStatus(item, cfg) {
  const repo = item.repo || item.cwd || '';
  const command = item.type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'agent-loop');
  if (process.platform === 'win32') {
    // agent-loop は WSL 側で動く想定なので、リポジトリが Windows ドライブ上でも WSL を探査する。
    const needle = repo ? (toWslCwd(repo) || repo) : itemId(item, 0);
    const distro = wslDistro(repo);
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; pgrep -af ${shellQuote(command)} | grep -F -- ${shellQuote(needle)} | grep -v grep | head -1`;
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script];
    const r = sh('wsl.exe', wslArgs, { timeoutMs: 8000 });
    return r.ok && r.stdout ? { running: true, detail: r.stdout } : { running: false, detail: '' };
  }
  const needle = repo ? wslPath(repo) : itemId(item, 0);
  const r = sh('sh', ['-lc', `pgrep -af ${shellQuote(command)} | grep -F -- ${shellQuote(needle)} | grep -v grep | head -1`], { timeoutMs: 8000 });
  return r.ok && r.stdout && r.stdout.includes(command) ? { running: true, detail: r.stdout } : { running: false, detail: '' };
}

// probeProcess=false（既定）: ログ mtime だけで状態推定。WSL への pgrep/wmic を毎ポーリングで撃たない。
function dynamicState(item, cfg, { probeProcess = false } = {}, config = null) {
  const repo = item.repo || item.cwd || '';
  const proc = probeProcess ? processStatus(item, cfg) : { running: false, detail: '' };
  const logs = repo ? listItemLogCandidates(item, config) : [];
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
    probed: !!probeProcess,
  };
}

// 業務ごとの実行エージェント設定（{ tier, agent_cli, model } | null）。
// 自動割り当て（resolveAgent / profiles）より優先する人の選択で、「今すぐ実行」の
// 一回限りの選択とは別物——ここに書かれた値だけが毎回の実行に効く。
function normalizeExecutionChoice(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const tier = String(raw.tier || '').trim();
  const agentCli = String(raw.agent_cli || '').trim();
  if (!tier || !agentCli) return null;
  return { tier, agent_cli: agentCli, model: String(raw.model || '').trim() };
}

// 発見項目（実体ファイルが正）の実行エージェント設定は、実体ファイルへ dashboard 語彙を
// 持ち込まず、dashboard 設定の対応表（cowork.executionChoices[項目id]）で持つ。
function executionChoicesOf(cfg) {
  const raw = cfg && cfg.executionChoices;
  if (!raw || typeof raw !== 'object') return {};
  const out = {};
  for (const [id, value] of Object.entries(raw)) {
    const choice = normalizeExecutionChoice(value);
    if (choice) out[String(id)] = choice;
  }
  return out;
}

// 項目に効いている永続設定（手動項目は項目自身、発見項目は対応表から）。
function storedExecutionChoice(config, item) {
  if (!item) return null;
  if (item.source === 'discovered') {
    return executionChoicesOf((config || {}).cowork || {})[String(item.id || '')] || null;
  }
  return normalizeExecutionChoice(item.executionChoice);
}

function normalizeItem(item, i, cfg, stateOpts, config) {
  const type = item.type === 'state-machine' ? 'state-machine' : 'loop';
  const id = itemId({ ...item, type }, i);
  const executionChoice = normalizeExecutionChoice(item.executionChoice);
  return {
    id,
    type,
    ...(executionChoice ? { executionChoice } : {}),
    name: String(item.name || item.id || (type === 'loop' ? `定期実行 ${i + 1}` : `定型業務 ${i + 1}`)),
    repo: item.repo || item.cwd || '',
    // 手動登録では人が選んだフォルダがそのまま登録フォルダ（実行の cwd）になる。
    root: item.root || item.repo || item.cwd || '',
    branch: item.branch || '',
    schedule: item.schedule || item.cron || '',
    workflow: item.workflow || item.file || '',
    prompt: item.prompt || '',
    instruction: item.instruction || '',
    description: item.description || '',
    command: type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'agent-loop'),
    source: 'config',
    state: dynamicState({ ...item, id, type }, cfg, stateOpts, config),
  };
}

// 発見項目（discover.js 由来）を Cowork 項目へ。source/_src/enabled を保持しつつ live state を付与。
function normalizeDiscovered(d, cfg, stateOpts, config) {
  const type = d.type === 'state-machine' ? 'state-machine' : 'loop';
  return {
    id: d.id,
    type,
    name: String(d.name || d.id),
    repo: d.repo || '',
    root: d.root || d.repo || '',
    branch: '',
    schedule: d.schedule || '',
    workflow: d.workflow || '',
    prompt: d.prompt || '',
    instruction: '',
    description: d.description || '',
    command: type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'agent-loop'),
    source: 'discovered',
    enabled: d.enabled !== false,
    _src: d._src,
    state: dynamicState({ ...d, type }, cfg, stateOpts, config),
  };
}

// 重複排除キー: type|repo実体|ジョブ名。先に並ぶ config 項目が発見項目に勝つ（手動登録が正）。
function jobKey(it) {
  const name = (it._src && (it._src.promptName || it._src.workflowName))
    || (it.type === 'state-machine' && it.workflow) || it.name || '';
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

function rawDiscovered(config, { forceDiscover = false } = {}) {
  const key = discoverCacheKey(config);
  const now = Date.now();
  if (!forceDiscover && _discoverCache.items && _discoverCache.key === key && (now - _discoverCache.at) < DISCOVER_TTL_MS) {
    return _discoverCache.items;
  }
  try {
    const items = discoverCoworkItems(config);
    _discoverCache = { key, at: now, items };
    return items;
  } catch {
    return [];   // 発見の失敗で overview 全体を壊さない
  }
}

function discoverNormalized(config, cfg, opts) {
  return rawDiscovered(config, opts).map((d) => normalizeDiscovered(d, cfg, opts, config));
}

// opts.probeProcess: true のときだけプロセス探査（実行直後・手動更新用）。ポーリングはログのみ。
// opts.forceDiscover: true で発見キャッシュを無視して再走査。
function overview(config, opts = {}) {
  const cfg = config.cowork || {};
  const stateOpts = { probeProcess: opts.probeProcess === true };
  const discoverOpts = { forceDiscover: opts.forceDiscover === true, ...stateOpts };
  const loop = makeLoopProvider(cfg, config);
  const addParameters = (item) => {
    const spec = routineParameterSpec(config, item);
    // 自動割り当ての結果は設定の有無に関わらず出す（編集画面が「自動ならこうなる」を
    // 具体的なエージェント・モデルで見せるため）。
    let autoExecution = { agent_cli: '', model: '' };
    try {
      const resolved = resolveRoutineAgent(config, item.repo || item.cwd || '');
      autoExecution = { agent_cli: resolved.cli || '', model: resolved.model || '' };
    } catch {
      // 表示用の解決失敗で一覧全体を壊さない。
    }
    const choice = storedExecutionChoice(config, item);
    let execution = autoExecution;
    let executionSource = 'auto';
    if (choice) {
      try {
        const resolved = resolveRoutineAgent(config, item.repo || item.cwd || '', choice);
        execution = { agent_cli: resolved.cli || '', model: resolved.model || '' };
        executionSource = 'user';
      } catch {
        // 候補の宣言が変わって設定が無効になったときは自動割り当てへ落として見せる
        // （実行時は resolveRoutineAgent が同じ理由で明示的に失敗する）。
      }
    }
    return {
      ...item,
      parameters: spec.keys,
      parameterError: spec.error,
      execution,
      executionSource,
      autoExecution,
      executionChoice: choice,
    };
  };
  const configItems = itemsOf(cfg).map((item, i) => addParameters(normalizeItem(item, i, cfg, stateOpts, config)));
  const discovered = discoverNormalized(config, cfg, discoverOpts).map(addParameters);
  const items = dedupeItems([...configItems, ...discovered]);
  const discoveredByKey = new Map();
  for (const item of discovered) {
    // 鍵は所属フォルダ（登録したフォルダ）。設定がサブフォルダにある作業でも、画面が
    // 「このフォルダに定常業務がある」と判定できるようにする。
    const folder = item.root || item.repo;
    if (folder) discoveredByKey.set(_pathKey(folder), folder);
  }
  return {
    loopProvider: loop.provider,
    loopCommand: loop.command,
    stateMachineCommand: cfg.stateMachineCommand || 'statemachine-use',
    // renderer が「選択プロジェクトに設定ファイルがあるか」を判定する根拠。
    // items は手動設定との重複排除で source=config になる場合があるため、発見元を別に保持する。
    discoveredRepos: [...discoveredByKey.values()],
    // アドホック起動の起動先候補（登録済みフォルダ。runAdhoc の検証と同じ集合）。
    roots: adhocRoots(config),
    ...routineTierOverview(config),
    items,
  };
}

function routineTierOverview(config) {
  try {
    const profile = profiles.load(config);
    const routineTiers = Object.entries(profile.tiers || {})
      .sort((a, b) => b[1].order - a[1].order)
      // 「今すぐ実行」は自動選択の結果だけを見る画面ではなく、今回だけ使う組み合わせを
      // 人が選ぶ画面。resolveTier で各段を先頭の実効候補へ潰さず、保存された全候補を渡す。
      .flatMap(([id, tier]) => (Array.isArray(tier.candidates) ? tier.candidates : [])
        .filter((candidate) => candidate && typeof candidate === 'object')
        .map((candidate) => ({ id, tier: id, label: tier.label || id, ...candidate })));
    const remembered = profile.state && profile.state.routine && profile.state.routine.tier;
    const rememberedCandidate = profile.state && profile.state.routine && profile.state.routine.candidate;
    const currentRoutineTier = routineTiers.some((tier) => tier.id === remembered)
      ? remembered : ((routineTiers[0] && routineTiers[0].id) || '');
    const currentRoutineCandidate = rememberedCandidate && typeof rememberedCandidate === 'object'
      ? {
        agent_cli: String(rememberedCandidate.agent_cli || ''),
        model: String(rememberedCandidate.model || ''),
      }
      : null;
    return { routineTiers, currentRoutineTier, currentRoutineCandidate };
  } catch {
    return { routineTiers: [], currentRoutineTier: '', currentRoutineCandidate: null };
  }
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
    return rawDiscovered(config).find((d) => d.id === String(id)) || null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// 実行プロンプトの解決（ウィンドウ実行 = agent-loop を介さず tmux + kiro-cli へ直接送る用）
// ---------------------------------------------------------------------------

const TEMPLATE_PARAMETER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g;
const LOOP_PARAMETER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}|(?<!\{)\{([A-Za-z_][A-Za-z0-9_.-]*)\}(?!\})/g;

function templateParameterKeys(...texts) {
  const keys = [];
  const seen = new Set();
  for (const text of texts) {
    for (const match of String(text || '').matchAll(TEMPLATE_PARAMETER_RE)) {
      if (!seen.has(match[1])) {
        seen.add(match[1]);
        keys.push(match[1]);
      }
    }
  }
  return keys;
}

function loopParameterKeys(...texts) {
  const keys = [];
  const seen = new Set();
  for (const text of texts) {
    for (const match of String(text || '').matchAll(LOOP_PARAMETER_RE)) {
      const key = match[1] || match[2];
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    }
  }
  return keys;
}

// ステートマシン定義（.statemachine/<name>/workflow.yaml）の参照先を解決する。
function stateMachineFilePath(item, cwd, config) {
  const src = item && item._src;
  if (src && src.kind === 'statemachine' && src.file) return viewerRepo(src.file, config) || src.file;
  const machine = String((item && (item.workflow || item.file || item.name)) || '').trim();
  if (!machine || /[\\/]/.test(machine)) return '';
  const root = viewerRepo(cwd, config) || String(cwd || '');
  return root ? path.join(root, '.statemachine', machine, 'workflow.yaml') : '';
}

function workflowReference(baseDir, value, fallback = '') {
  let ref = scalarString(value) || fallback;
  if (ref.startsWith('file:')) ref = ref.slice(5).trim();
  if (!ref) return '';
  const file = path.resolve(baseDir, ref);
  const relative = path.relative(baseDir, file);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`定義フォルダ外の参照は使えません: ${ref}`);
  }
  return file;
}

function readWorkflowReference(baseDir, value, fallback = '', optional = false) {
  const file = workflowReference(baseDir, value, fallback);
  if (!file) return '';
  try { return fs.readFileSync(file, 'utf8'); } catch {
    if (optional) return '';
    throw new Error(`参照ファイルを読めません: ${path.relative(baseDir, file)}`);
  }
}

function contextValue(context, key) {
  if (Object.prototype.hasOwnProperty.call(context, key)) return context[key];
  let value = context;
  for (const part of String(key || '').split('.')) {
    if (!isPlainObject(value) || !Object.prototype.hasOwnProperty.call(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

// statemachine 実行器が自分で作って注入する変数。workflow の `context:` に値が無く、
// action / condition / on_enter / on_exit から参照されていても**ユーザー入力として要求しない**
// ——人が入れる値ではなく、実行器が実行開始時とステート実行中に生成するため。
// 正典は statemachine-use の references/schema.md「Context Variable Reference」と、
// それを実装する engine.py / agent-loop の statemachine ハーネス（_sm_initial_context）。
//   実行開始時に注入: today / now / history / step_count / last_output / current_state / context
//   ステート実行中に注入: 決定的検査（check）の check_status / check_ok / check_output
// 履歴変数（history.<state_id>）とステート出力変数（output_key）は参照側で別に除く。
// `input` はここへ入れない——実行器ではなく人が渡す値（`--input`）なので必須入力のまま。
const RUNTIME_CONTEXT_KEYS = new Set([
  'today', 'now', 'history', 'step_count', 'last_output', 'current_state', 'context',
  'check_status', 'check_ok', 'check_output',
]);

// statemachine-use の正式なテンプレート面だけを読む。action_file / condition_file と
// 自動探索ファイルもエンジンと同じ優先順で解決し、実行中に生成される変数は入力にしない。
function stateMachineInputSpec(wfPath) {
  if (!wfPath) return { keys: [], defaults: {}, error: 'workflow.yaml の場所を特定できません' };
  try {
    const text = fs.readFileSync(wfPath, 'utf8');
    const workflow = parseYaml(text);
    if (!isPlainObject(workflow)) return { keys: [], defaults: {}, error: 'workflow.yaml を解析できません' };
    const baseDir = path.dirname(wfPath);
    const templates = [];
    const outputKeys = new Set();
    const ruleKeys = [];
    const states = isPlainObject(workflow.states) ? workflow.states : {};
    for (const [stateId, state] of Object.entries(states)) {
      if (!isPlainObject(state)) continue;
      const actionFile = scalarString(state.action_file);
      const action = scalarString(state.action);
      if (actionFile) templates.push(readWorkflowReference(baseDir, actionFile));
      else if (action && action.trim().startsWith('file:')) templates.push(readWorkflowReference(baseDir, action));
      else if (action) templates.push(action);
      else templates.push(readWorkflowReference(baseDir, '', `actions/${stateId}.md`, true));
      templates.push(scalarString(state.on_enter) || '', scalarString(state.on_exit) || '');
      const outputKey = scalarString(state.output_key);
      if (outputKey) outputKeys.add(outputKey);
    }
    for (const transition of Array.isArray(workflow.transitions) ? workflow.transitions : []) {
      if (!isPlainObject(transition)) continue;
      const conditionFile = scalarString(transition.condition_file);
      const condition = scalarString(transition.condition);
      if (conditionFile) templates.push(readWorkflowReference(baseDir, conditionFile));
      else if (condition && condition.trim().startsWith('file:')) templates.push(readWorkflowReference(baseDir, condition));
      else if (condition) templates.push(condition);
      else {
        const from = scalarString(transition.from) || '';
        const to = scalarString(transition.to) || '';
        const auto = from && to ? `conditions/${from === '*' ? 'wildcard' : from}_to_${to}.md` : '';
        templates.push(readWorkflowReference(baseDir, '', auto, true));
      }
      for (const rule of String(scalarString(transition.condition_rule) || '').split(';')) {
        const key = rule.split(':')[1];
        if (key) ruleKeys.push(key.trim());
      }
    }

    const refs = [...templateParameterKeys(...templates), ...ruleKeys];
    const context = isPlainObject(workflow.context) ? workflow.context : {};
    const keys = [];
    const defaults = {};
    for (const ref of [...new Set(refs)]) {
      if (ref === 'input') {
        keys.push(ref);
        continue;
      }
      if (RUNTIME_CONTEXT_KEYS.has(ref) || ref.startsWith('history.') || outputKeys.has(ref)) continue;
      const contextKey = ref.startsWith('context.') ? ref.slice('context.'.length) : ref;
      const value = contextValue(context, contextKey);
      if (value === undefined || value === null || String(value).trim() === '') keys.push(ref);
      else defaults[ref] = String(value);
    }
    return { keys: [...new Set(keys)], defaults, error: '' };
  } catch (err) {
    return { keys: [], defaults: {}, error: err.message || String(err) };
  }
}

function loopPrompt(item, config) {
  const name = item.source === 'discovered'
    ? ((item._src && item._src.promptName) || item.name)
    : (item.name || item.id);
  return String(item.prompt || resolveLoopPromptText(item.repo || item.cwd, name, config) || '');
}

function routineParameterSpec(config, item) {
  if (Array.isArray(item.args)) return { keys: [], defaults: {}, error: '' };
  if (item.type !== 'state-machine') {
    return { keys: loopParameterKeys(loopPrompt(item, config)), defaults: {}, error: '' };
  }
  const cwd = launchCwd(item, config) || process.cwd();
  const spec = stateMachineInputSpec(stateMachineFilePath(item, cwd, config));
  if (spec.error) return spec;
  const pairedName = item._src && item._src.loop && item._src.loop.promptName;
  const pairedBody = pairedName ? resolveLoopPromptText(item.repo || item.cwd, pairedName, config) : '';
  for (const key of loopParameterKeys(pairedBody)) {
    if (!Object.prototype.hasOwnProperty.call(spec.defaults, key) && !spec.keys.includes(key)) spec.keys.push(key);
  }
  return spec;
}

function validateParameters(spec, raw) {
  if (spec.error) throw new Error(`入力パラメータを確認できません: ${spec.error}`);
  const values = raw == null ? {} : raw;
  if (!isPlainObject(values)) throw new Error('入力パラメータの形式が不正です');
  const unknown = Object.keys(values).filter((key) => !spec.keys.includes(key));
  if (unknown.length) throw new Error(`未定義の入力パラメータです: ${unknown.join(', ')}`);
  const missing = spec.keys.filter((key) => !Object.prototype.hasOwnProperty.call(values, key)
    || String(values[key]).trim() === '');
  if (missing.length) throw new Error(`入力してください: ${missing.join(', ')}`);
  return Object.fromEntries(spec.keys.map((key) => [key, String(values[key]).trim()]));
}

function applyParameters(prompt, values) {
  return String(prompt || '').replace(LOOP_PARAMETER_RE, (whole, doubleKey, singleKey) => {
    const key = doubleKey || singleKey;
    return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : whole;
  });
}

function stateMachineParameterBlock(values) {
  const entries = Object.entries(values);
  if (!entries.length) return '';
  return '\n\n以下の実行パラメータをそのまま使用してください。追加質問や仮値補完は不要です。\n'
    + entries.map(([key, value]) => `- ${key}: ${JSON.stringify(value)}`).join('\n');
}

// ハーネスへ渡すワークフローの位置。**実行 cwd からの相対 POSIX パス**にする——
// ハーネスは WSL 側で動くので、ビュアー（Windows）のパスをそのまま渡すと解決できない。
// 相対にしておけば、cwd さえ WSL 側へ翻訳できていればディストロ表記に依存しない。
//
// 作業フォルダの外を指してしまう組み合わせ（登録フォルダと定義フォルダが別ボリューム
// にある等。win32 の path.relative は道筋が無いと絶対パスを返す）はここで断る。
// そのまま渡すと WSL 側で意味を成さないパスになり、ハーネスの「作業フォルダ外」
// エラーだけが返って原因が分からない。
function harnessWorkflowArg(cwd, workflowPath, config) {
  const rel = relPosix(cwd, workflowPath, config);
  if (!rel || rel === '..' || rel.startsWith('../') || /^(?:[A-Za-z]:|\/)/.test(rel)) {
    throw new Error(`ステートマシン定義が作業フォルダの外にあります: ${workflowPath}`);
  }
  return rel;
}

// agent-loop statemachine ハーネス（限定ツールループ）の起動引数。
// モデルは段に宣言されているときだけ --model で今回の実行に明示する。
// 空欄なら agent-loop / CLI 定義の default_model に任せる。
function stateMachineHarnessArgs(cwd, workflowPath, selected, values, config) {
  const args = ['statemachine', '--workflow', harnessWorkflowArg(cwd, workflowPath, config),
    '--agent-cli', selected.cli];
  if (selected.model) args.push('--model', String(selected.model));
  for (const [key, value] of Object.entries(values || {})) {
    args.push('--param', `${key}=${value}`);
  }
  return args;
}

// グローバル指示（agent-instructions 契約）を起動プロンプト先頭へ前置する。
// dashboard は指示の書き手であると同時に、自分が起動する CLI に対する読み手でもある
// （cowork の定常業務 / 定型業務ウィンドウ）。二重注入・空・破損はすべて no-op。
// config には .orchestration が載る（instructionsDir 解決に使う）。
function withGlobalInstructions(config, prompt) {
  const p = String(prompt || '');
  if (!p) return p;
  try {
    const dir = globalInstructions.resolveInstructionsDir(config || {});
    const block = globalInstructions.renderBlock(globalInstructions.loadInstructions(dir));
    return globalInstructions.prependBlock(p, block);
  } catch {
    return p; // フェイルセーフ: 指示の描画失敗で起動を止めない
  }
}

// セッション開始コマンド（agent-session-commands 契約）の実行計画。tmux セッションを新しく
// 作るときだけ走るシェル片へ落とすため、ここで when 判定とプレースホルダ展開まで済ませる。
// 不在・破損・無効はすべて空配列（＝何も差し込まない）。
// agentCli は「どの CLI へ送るか」で決まる。定常業務ウィンドウも CLIチャット・対話診断と
// 同じく**解決済みの CLI**（coworkChatLaunch の cli）を呼び出し側が渡す——ここを 'kiro' に
// 固定していると、ollama で起動したセッションへ kiro 向けの開始コマンドが送られる。
function planSessionCommands(config, cwd, { agentCli = 'kiro', skillCommandPrefix = '/' } = {}) {
  try {
    const dir = sessionCommands.resolveSessionDir(config || {});
    // コマンドは WSL 側のシェルで走るため、{cwd} / {workspace} は Linux パスへ揃える
    // （Windows パスのまま渡すと cd も git -C も刺さらない）。
    const linuxCwd = toWslCwd(cwd) || String(cwd || '');
    return sessionCommands.plan(sessionCommands.loadSessionCommands(dir), {
      engine: 'dashboard',
      workload: 'routine',
      cwd: linuxCwd,
      workspace: linuxCwd,
      agent_cli: agentCli,
      skill_command_prefix: skillCommandPrefix,
    });
  } catch {
    return []; // フェイルセーフ: 計画の失敗で起動を止めない
  }
}

// repo の定常業務ループ設定（discover.js の LOOP_CONFIG_CANDIDATES）から定期プロンプト
// 本文を名前で解決する。
// 見つからなければ ''（呼び出し側が代替の指示文へフォールバックする）。
function resolveLoopPromptText(repo, promptName, config) {
  const root = viewerRepo(repo, config) || String(repo || '');
  const name = String(promptName || '').trim();
  if (!root || !name) return '';
  let marker;
  try { marker = detectMarkers(root); } catch { return ''; }
  if (!marker || !marker.kiroFile) return '';
  let text;
  try { text = fs.readFileSync(marker.kiroFile, 'utf8'); } catch { return ''; }
  if (marker.kiroFormat === 'json') {
    try {
      const arr = JSON.parse(text);
      const prompts = Array.isArray(arr && arr.prompts) ? arr.prompts : [];
      const hit = prompts.find((p) => p && String(p.name || '') === name);
      return hit ? String(hit.prompt == null ? '' : hit.prompt) : '';
    } catch { return ''; }
  }
  const norm = text.replace(/\r\n/g, '\n');
  const entries = parseAgentLoopPrompts(norm);
  const texts = agentLoopPromptTexts(norm);
  // 発見項目の既定名（prompt-<n>）でも引けるようにインデックス名も突き合わせる
  const idx = entries.findIndex((e, i) => (e.name || `prompt-${i + 1}`) === name);
  return idx >= 0 ? String(texts[idx] || '') : '';
}

// 同じ設定から受入条件（acceptance）を名前で解決する。ツールループ非内蔵の CLI
// （`headless_autonomy: single-shot`）では、これが無いと done を機械検証できない
// （agent-loop 設計「層3 のツールループと受入条件」）。読めなければ空配列。
// 読みは本物の YAML パーサを通す（writeback.js の行指向パーサは書き戻し専用）。
function resolveLoopAcceptance(repo, promptName, config) {
  const root = viewerRepo(repo, config) || String(repo || '');
  const name = String(promptName || '').trim();
  if (!root || !name) return [];
  let marker;
  try { marker = detectMarkers(root); } catch { return []; }
  if (!marker || !marker.kiroFile) return [];
  let text;
  try { text = fs.readFileSync(marker.kiroFile, 'utf8'); } catch { return []; }
  let data;
  if (marker.kiroFormat === 'json') {
    try { data = JSON.parse(text); } catch { return []; }
  } else {
    data = parseYaml(text);
  }
  const prompts = Array.isArray(data && data.prompts) ? data.prompts : [];
  const hit = prompts.find((p, i) => p && typeof p === 'object'
    && (String(p.name || '') === name || `prompt-${i + 1}` === name));
  // 1 件だけのときスカラーで書ける（agent-loop の validate_entries と同じ受け方）。
  // ここを配列限定にしていると、書いてあるのに「受入条件が無い」と言われる。
  const raw = hit ? hit.acceptance : null;
  const list = Array.isArray(raw) ? raw : (raw == null || raw === '' ? [] : [raw]);
  return list.map((v) => String(v == null ? '' : v).trim()).filter(Boolean);
}

// 実行時の cwd は**登録したフォルダ**（`root`）。設定ファイルがサブフォルダにあっても、
// エージェントは人が登録したフォルダで起きる——そこが人の言う「この作業のフォルダ」で、
// 画面の所属（サイドバーで選ぶフォルダ）とも一致する。プロンプト本文とログの読み取りは
// 設定のあるフォルダ（`repo`）のままで、こちらは変えない。
function launchCwd(item, config) {
  const dir = item.root || item.repo || item.cwd || '';
  return viewerRepo(dir, config) || dir;
}

function runLoop(config, itemIdValue, parameters, tier = '') {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  // 優先順: 今回だけの選択（今すぐ実行）＞業務の設定＞自動割り当て。
  tier = tier || storedExecutionChoice(config, item) || '';
  const spec = routineParameterSpec(config, item);
  const values = validateParameters(spec, parameters);
  // 実行対象は agent-loop の prompt 名（合成 id ではない）。`send` が cwd の
  // .agents/agent-loop.* から名前解決するため、手動項目も表示名を優先する。
  const runId = item.source === 'discovered'
    ? ((item._src && item._src.promptName) || item.name)
    : (item.name || item.id);
  const cwd = launchCwd(item, config);
  // ウィンドウ実行用: 定常業務の設定からプロンプト本文を解決して直接送る（ループ CLI 非経由）。
  // 本文を解決できなければ、エージェント自身に設定を読ませる指示文で代替する。
  // 明示 args の項目は従来の <loopCommand> 実行のまま（prompt を渡さない）。
  const resolvedPrompt = applyParameters(
    loopPrompt(item, config)
      || `.agents/agent-loop.yml（または agent-loop.yaml / .json）の定期プロンプト「${runId}」の本文を読んで、その指示を実行して`,
    values
  );
  const prompt = Array.isArray(item.args) ? undefined : withGlobalInstructions(config, resolvedPrompt);
  const selected = resolveRoutineAgent(config, cwd, tier);
  if (prompt && needsHeadlessHarness(selected.spec)) {
    // 対話ペインを持たない CLI（aider・素の ollama）。**tmux とは無関係**——tmux は
    // コマンドを送り結果を見せる手段なので、ここも定型業務と同じウィンドウ経路で見せる。
    // 変わるのは中で走らせるものだけで、対話 CLI の代わりに agent-loop の単発実行
    // （`run`。ツールループ非内蔵の CLI には限定ツール契約でツール実行を供給する）を起こす。
    return runHeadlessRoutine(config, {
      cwd,
      prompt,
      acceptance: resolveLoopAcceptance(item.repo || item.cwd, runId, config),
      selected,
      title: '定常業務を実行',
      timeoutMs: item.timeoutMs,
      record: (res) => recordRun(cfg, { ...item, id: runId, type: 'loop' }, res),
    });
  }
  const plan = routineLaunchPlan(config, cwd, tier, selected);
  const res = makeLoopProvider(cfg, config).run({
    ...item, cwd, id: runId, prompt,
    ...(Object.keys(values).length ? { args: ['send', prompt] } : {}),
    launch: plan.launch, sessionCommands: plan.sessionCommands,
  });
  recordRun(cfg, { ...item, type: 'loop' }, res);
  return res;
}

// 単発 headless 実行（`agent-loop run`）を tmux ウィンドウで起こす。定型業務の
// ステートマシンハーネス起動と同じ形——起動器を 2 つ持たない（C7）。ウィンドウを開けない
// 環境（CI 等）は結果契約（RESULT 行）を待つ非同期実行へ落とす。
// 履歴の書き方は呼び出し側で違う（定常業務は項目に紐づき、アドホックは紐づかない）ので、
// 記録は `record` で受け取る——同期・非同期のどちらでも 1 回だけ呼ばれる。
function runHeadlessRoutine(config, { cwd, prompt, acceptance, selected, title, timeoutMs, record }) {
  const cfg = config.cowork || {};
  const args = ['run', prompt, '--agent-cli', selected.cli];
  if (selected.model) args.push('--model', String(selected.model));
  for (const criterion of acceptance || []) args.push('--acceptance', String(criterion));
  const command = cfg.loopCommand || cfg.loopProvider || 'agent-loop';
  if (cfg.runWindow !== false && supportsRunWindow()) {
    const res = runCommandWindow({
      command,
      args,
      cwd,
      sessionKey: `${selected.cli}:${cwd}`,
      title,
      message: `別ウィンドウ（tmux）で ${selected.cli} の単発実行を開始しました`,
    });
    record(res);
    return res;
  }
  return runCommandCapture(command, args, { cwd, timeoutMs: timeoutMs || 1800000 })
    .then((res) => {
      record(res);
      return res;
    });
}

function runStateMachine(config, itemIdValue, parameters, tier = '') {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 定型業務が見つかりません: ${itemIdValue}`);
  // 優先順: 今回だけの選択（今すぐ実行）＞業務の設定＞自動割り当て。
  tier = tier || storedExecutionChoice(config, item) || '';
  const cwd = launchCwd(item, config) || process.cwd();
  const spec = routineParameterSpec(config, item);
  // 旧 preload の文字列 input を読む互換。新しい画面は常に key/value オブジェクトを渡す。
  const raw = typeof parameters === 'string' ? (parameters ? { input: parameters } : {}) : parameters;
  const values = validateParameters(spec, raw);
  const effectiveValues = { ...spec.defaults, ...values };
  // statemachine-use は CLI ではなくスキル。エージェントセッションへ
  // 「statemachine-use スキルで xxx ステートマシンを実行して」を送って発動する。
  // ループ設定に対となる定期プロンプトがある統合項目はその本文を優先する。
  let args;
  let prompt;
  const pairedName = item._src && item._src.loop && item._src.loop.promptName;
  if (Array.isArray(item.args)) {
    args = [...item.args];
    // 明示 args はレガシー実行（prompt を渡さない）
  } else {
    const smName = item.workflow || item.file || item.name;
    const pairedBody = pairedName ? resolveLoopPromptText(item.repo || item.cwd, pairedName, config) : '';
    const smPrompt = applyParameters(
      pairedBody || `statemachine-use スキルで${smName}ステートマシンを実行して`,
      effectiveValues
    ) + stateMachineParameterBlock(values);
    prompt = withGlobalInstructions(config, smPrompt);
    // 非ウィンドウ実行（非 win32 / runWindow:false）用の従来 send 引数も併せて用意する
    const legacy = !Object.keys(values).length
      ? (pairedName || `${smName} ステートマシンを実行して`)
      : smPrompt;
    args = ['send', legacy];
  }
  const selected = resolveRoutineAgent(config, cwd, tier);
  if (needsHeadlessHarness(selected.spec)) {
    if (Array.isArray(item.args)) throw new Error('明示 args の定型業務は headless 実行に対応していません');
    const workflowPath = stateMachineFilePath(item, item.repo || item.cwd || cwd, config);
    if (!workflowPath) throw new Error('workflow.yaml の場所を特定できません');
    // 対話セッションを持たない CLI（aider・素の ollama）は、agent-loop の statemachine
    // ハーネス（限定ツールループ）へ実行契約を渡す。**tmux ウィンドウで見せるのは変わらない**
    // ——tmux はコマンドを送り結果を見せる手段で、対話 CLI 専用の仕組みではない。
    // 段にモデルが宣言されているときだけ --model で今回の実行に明示する。
    // 空欄なら agent-loop / CLI 定義の default_model に任せる。
    const smArgs = stateMachineHarnessArgs(cwd, workflowPath, selected, effectiveValues, config);
    const command = cfg.loopCommand || cfg.loopProvider || 'agent-loop';
    if (cfg.runWindow !== false && supportsRunWindow()) {
      const res = runCommandWindow({
        command,
        args: smArgs,
        cwd,
        sessionKey: selected.cli,
        title: '定型業務を実行',
        message: '別ウィンドウ（tmux）で Aider ステートマシン実行を開始しました',
      });
      recordRun(cfg, { ...item, type: 'state-machine' }, res);
      return res;
    }
    // ウィンドウを開けない環境（CI 等）: 非同期に完走させ、結果契約（RESULT 行）を返す。
    return runCommandCapture(command, smArgs, { cwd, timeoutMs: item.timeoutMs || 1800000 })
      .then((res) => {
        recordRun(cfg, { ...item, type: 'state-machine' }, res);
        return res;
      });
  }
  const plan = routineLaunchPlan(config, cwd, tier, selected);
  const res = makeLoopProvider(cfg, config).run({
    ...item, cwd, args, prompt, timeoutMs: item.timeoutMs || 60000,
    launch: plan.launch, sessionCommands: plan.sessionCommands,
  });
  recordRun(cfg, { ...item, type: 'state-machine' }, res);
  return res;
}

function stateMachineCreationPrompt(name, machine, instruction) {
  return `statemachine-use スキルの作成モードで、次の指示から「${name}」ステートマシンを作成してください。\n`
    + `生成先は .statemachine/${machine}/ とし、作成だけを行って実行はしないでください。\n\n`
    + `指示:\n${instruction}`;
}

function generateStateMachine(config, payload = {}) {
  const repo = String(payload.repo || '').trim();
  const name = String(payload.name || '').trim();
  const machine = String(payload.machine || '').trim();
  const instruction = String(payload.instruction || '').trim();
  if (!repo || !name || !instruction) throw new Error('名前と定型業務の手順を入力してください');
  if (!/^[A-Za-z0-9_.-]+$/.test(machine) || machine === '.' || machine === '..') {
    throw new Error('定型業務の識別名が不正です');
  }
  const gitRoot = gitInRepo(repo, ['rev-parse', '--show-toplevel'], 10000, config);
  if (!gitRoot.ok) throw new Error('選択中の作業フォルダは Git リポジトリではありません');
  const prompt = withGlobalInstructions(config, stateMachineCreationPrompt(name, machine, instruction));
  const plan = routineLaunchPlan(config, repo);
  return runChatWindow({
    ...plan.launch,
    prompt,
    cwd: repo,
    sessionCommands: plan.sessionCommands,
    sessionKey: 'statemachine-builder',
    title: '定型業務を作成',
    message: '外部ターミナルで定型業務の作成を開始しました',
  });
}

// ---------------------------------------------------------------------------
// アドホック起動（M2）: 登録フォルダ + 自由文で対話 CLI を起動する。
// 項目（items）に依存しない generateStateMachine と同じ形で、新しい起動系は作らない
// ——CLI/モデル解決（routineLaunchPlan）・共通指示の前置・履歴の追記を既存経路のまま通す。
// ---------------------------------------------------------------------------

// 登録済みフォルダ（走査ルートと同じ集合）を解決済みパスで返す。renderer の選択肢と
// runAdhoc の cwd 検証が同じ集合を見る——cwd は登録済みフォルダからの選択のみ（C1）。
function adhocRoots(config) {
  const seen = new Set();
  const out = [];
  for (const r of coworkRoots(config)) {
    const folder = _resolveRoot(r, config);
    if (!folder) continue;
    const k = _pathKey(folder);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(folder);
  }
  return out;
}

function runAdhoc(config, payload = {}) {
  const cfg = config.cowork || {};
  const rawRoot = String(payload.root || '').trim();
  const freeText = String(payload.prompt || '').trim();
  if (!rawRoot) throw new Error('フォルダを選択してください');
  if (!freeText) throw new Error('エージェントへの指示を入力してください');
  const folder = _resolveRoot(rawRoot, config);
  const registered = adhocRoots(config).find((r) => _pathKey(r) === _pathKey(folder));
  if (!registered) throw new Error(`登録されていないフォルダは起動先にできません: ${rawRoot}`);
  const prompt = withGlobalInstructions(config, freeText);
  const selected = resolveRoutineAgent(config, registered);
  // 履歴は項目に紐づかないので、名前はプロンプトの 1 行目で代える。
  const name = freeText.split(/\r?\n/)[0].slice(0, 60);
  const record = (res) => appendHistory(cfg, {
    at: new Date().toISOString(),
    key: jobKey({ type: 'adhoc', repo: registered, name }),
    id: '',
    name,
    type: 'adhoc',
    repo: registered,
    ok: !!(res && res.ok),
    message: String((res && (res.error || res.message)) || '').trim().slice(0, 300),
  });
  // 対話ペインを持たない CLI（段の降格で aider へ落ちた場合など）も同じウィンドウで見せる。
  // 起動するものが対話セッションか単発実行かの違いだけで、経路は分けない。
  if (needsHeadlessHarness(selected.spec)) {
    return runHeadlessRoutine(config, {
      cwd: registered, prompt, acceptance: [], selected, title: 'アドホック起動', record,
    });
  }
  const plan = routineLaunchPlan(config, registered, '', selected);
  const res = runChatWindow({
    ...plan.launch,
    prompt,
    cwd: registered,
    sessionCommands: plan.sessionCommands,
    // 定常業務のセッション（kiro-dash-…）へ合流させない。進行中の会話に自由文を
    // 混ぜないよう、アドホック専用の名前空間で開く。
    sessionKey: 'adhoc',
    title: 'アドホック起動',
    message: '外部ターミナルでエージェントCLIを起動しました',
  });
  record(res);
  return res;
}

// 定常業務（agent-control 契約のワークロード名）。全体設定 → 実行制御の
// 「機能ごとのエージェントとモデル」で routine に書いた宣言が、この画面の起動に効く。
const ROUTINE_WORKLOAD = 'routine';

// 旧 cowork.chatCommand の既定値。**既定値として**配っていたせいで saveConfig が
// 全ユーザーの config.json へ書き戻し、明示上書きが常に載っている状態になっていた
// （＝定常業務だけが全体設定を無視して常に kiro-cli で起動していた不具合）。
// 既定は空文字へ改めたが、既存の config.json には値が残るので、この文字列に限っては
// 「人が選んだ上書き」ではなく既定の残骸とみなして無視する。
const LEGACY_CHAT_COMMAND = 'kiro-cli chat --trust-all-tools';

// ツールループを内蔵しない CLI は、対話ペインではなく限定ツール契約のハーネス
// （agent-loop の run / statemachine）で回す。
//
// **判定は定義の headless_autonomy で行う。interactive の有無で代理してはいけない。**
// 対話面を提供するか（interactive）と、自分で探索・実行まで回せるか（headless_autonomy）は
// 別の宣言である。両者を同じフラグで表すと、aider のように「対話もできるが
// ヘッドレスでは single-shot」の CLI を取り違え、定型業務が黙ってハーネスから対話送信へ
// 切り替わる（実際 2026-08-25 に aider.json へ interactive を足して踏んだ）。
//
// interactive を持たない CLI はそもそもペインで駆動しようが無いので、こちらもハーネスへ。
function needsHeadlessHarness(spec) {
  if (!spec || !spec.interactive) return true;
  // JS ローダ（agentCli.js）は camelCase へ正規化する。定義ファイルの綴りは
  // headless_autonomy だが、ここへ来る spec は正規化済みなので headlessAutonomy。
  return String(spec.headlessAutonomy || 'single-shot') === 'single-shot';
}

// 定常業務の tmux 実行で使う対話 CLI の一式（S9-3）。
//
// 起動する CLI とモデルの解決順は CLIチャット・対話診断と同じ interactiveLaunchSpec に
// 集約する（＝全体設定の workloads.routine.agent_cli / model が最優先）。ollama のように
// 起動 argv にモデル名が要る CLI（`agent-ollama --tui --think on <model>`）でも、
// 定義（agents/<name>.json の interactive）どおりの argv がそのまま組み上がる。
// `cowork.chatCommand` は明示上書き（空なら解決結果）。
// 解決できないとき（定義が見つからない等）は従来の文字列へ落として、定常業務を止めない。
// ただし黙っては落とさず、フォールバックした事実を警告ログに残す。
function resolveRoutineAgent(config, repo, executionChoice = '') {
  const agent = require('../../agent-project/main/agent');
  const base = agent.resolveAgent(config, repo, { workload: ROUTINE_WORKLOAD });
  const requested = executionChoice && typeof executionChoice === 'object'
    ? executionChoice : { tier: executionChoice };
  const selectedTier = String(requested.tier || '').trim();
  if (!selectedTier) return base;
  const profile = profiles.load(config);
  const tierSpec = (profile.tiers || {})[selectedTier];
  if (!tierSpec) {
    throw new Error(`段「${selectedTier}」は定義されていません`);
  }
  const explicitlySelected = executionChoice && typeof executionChoice === 'object';
  const candidate = explicitlySelected
    ? (Array.isArray(tierSpec.candidates) ? tierSpec.candidates : []).find((item) => item
      && String(item.agent_cli || '') === String(requested.agent_cli || '')
      && String(item.model || '') === String(requested.model || ''))
    : profiles.resolveTier(config, selectedTier);
  if (explicitlySelected && !candidate) {
    throw new Error(`段「${selectedTier}」に選択したエージェントとモデルの候補が定義されていません`);
  }
  if (!candidate) throw new Error(`段「${selectedTier}」には現在実行できる候補がありません`);
  const cli = candidate.agent_cli || base.cli;
  return {
    ...base,
    cli,
    // モデル空欄は「選択した CLI の既定」。base のモデルを補うと、
    // codex / gpt-* から aider へ切り替えたときに aider / gpt-* という不正な組み合わせになる。
    model: candidate.model || '',
    spec: agentCli.loadCli(cli, repo),
    source: `tier:${selectedTier}`,
    tier: selectedTier,
  };
}

function coworkChatLaunch(config, repo, tier = '', selected = null) {
  const explicit = String(((config || {}).cowork || {}).chatCommand || '').trim();
  if (!tier && !selected && explicit && explicit !== LEGACY_CHAT_COMMAND) {
    // 明示上書きでは CLI 名が分からない。開始コマンドの when.agent_cli 判定は
    // 従来どおり kiro とみなす（この経路は人が自分でコマンドを決めた場合だけ）。
    return { chatCommand: explicit, cli: 'kiro', model: '', skillCommandPrefix: '/' };
  }
  try {
    const { interactiveLaunchSpec } = require('../../agent-project/main/agent');
    const resolved = selected || resolveRoutineAgent(config, repo, tier);
    const launch = interactiveLaunchSpec(config, repo, { workload: ROUTINE_WORKLOAD, resolved });
    return {
      chatCommand: launch.chatCommand,
      readyPattern: launch.readyPattern,
      readyTimeoutSec: launch.readyTimeoutSec,
      cli: launch.cli,
      model: launch.model,
      source: launch.source,
      skillCommandPrefix: launch.skillCommandPrefix,
    };
  } catch (e) {
    if (tier || selected) throw e;
    console.warn(`[cowork] agent_cli 定義の解決に失敗したため既定の '${LEGACY_CHAT_COMMAND}' へフォールバックしました: ${(e && e.message) || e}`);
    return { chatCommand: LEGACY_CHAT_COMMAND, cli: 'kiro', model: '', skillCommandPrefix: '/' };
  }
}

// 定常業務ウィンドウ 1 回分の起動条件（対話 CLI の一式 + その CLI 向けの開始コマンド計画）。
// 起動する CLI が決まらないと開始コマンドの when.agent_cli 判定もスキル起動記号も決まらない
// ので、2 つを 1 か所で組む。
function routineLaunchPlan(config, cwd, tier = '', selected = null) {
  const launch = coworkChatLaunch(config, cwd, tier, selected);
  return {
    launch,
    sessionCommands: planSessionCommands(config, cwd, {
      agentCli: launch.cli || 'kiro',
      skillCommandPrefix: launch.skillCommandPrefix || '/',
    }),
  };
}

function gitInRepo(repo, args, timeoutMs, config) {
  if (process.platform === 'win32' && isWslPath(repo)) {
    const distro = wslDistro(repo);
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; cd ${shellQuote(wslPath(repo))} && git ${args.map(shellQuote).join(' ')}`;
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script];
    return sh('wsl.exe', wslArgs, { timeoutMs });
  }
  return sh('git', ['-C', viewerRepo(repo, config) || repo, ...args], { timeoutMs });
}

// 指定ファイル（repo 相対 POSIX パス）に差分があればそれだけを commit する。無ければ skip。
function gitCommitFiles(repo, relFiles, message, config) {
  if (!relFiles.length) return { ok: true, skipped: true };
  const st = gitInRepo(repo, ['status', '--porcelain', '--', ...relFiles], 10000, config);
  if (!st.ok) return { ok: false, step: 'status', error: st.stderr || st.error };
  if (!st.stdout.trim()) return { ok: true, skipped: true };
  const add = gitInRepo(repo, ['add', '--', ...relFiles], 30000, config);
  if (!add.ok) return { ok: false, step: 'add', error: add.stderr || add.error };
  const ci = gitInRepo(repo, ['commit', '-m', message, '--', ...relFiles], 30000, config);
  if (!ci.ok) return { ok: false, step: 'commit', error: ci.stderr || ci.error };
  return { ok: true };
}

function gitSave(repo, { branch, createBranch, push } = {}, config) {
  if (!repo) return { skipped: true };
  const current = gitInRepo(repo, ['rev-parse', '--abbrev-ref', 'HEAD'], 10000, config);
  if (!current.ok) return { ok: false, error: current.stderr || current.error || 'git rev-parse failed' };
  let checkout = null;
  if (branch) {
    checkout = gitInRepo(repo, createBranch ? ['checkout', '-B', branch] : ['switch', branch], 30000, config);
    if (!checkout.ok) return { ok: false, step: 'checkout', error: checkout.stderr || checkout.error };
  }
  let pushed = null;
  if (push) {
    const b = branch || current.stdout;
    pushed = gitInRepo(repo, ['push', '-u', 'origin', b], 120000, config);
    if (!pushed.ok) return { ok: false, step: 'push', error: pushed.stderr || pushed.error };
  }
  return { ok: true, branch: branch || current.stdout, checkout, pushed };
}

// repo からの相対 POSIX パス（WSL の git でも -C の git でも解決できる形）。
function relPosix(repo, file, config) {
  const root = viewerRepo(repo, config) || repo;
  return path.relative(root, file).split(path.sep).join('/');
}

// JSON 形式の agent-loop 設定へ発見項目の編集を反映（parse → mutate → stringify）。
function applyKiroLoopJson(raw, items) {
  let obj;
  try { obj = JSON.parse(raw); } catch { return { text: raw, changed: false, errors: ['agent-loop.json の解析に失敗'] }; }
  const prompts = Array.isArray(obj && obj.prompts) ? obj.prompts : [];
  let changed = false;
  for (const it of items) {
    const p = prompts[it._src.promptIndex];
    if (!p || typeof p !== 'object') continue;
    if ((it.name || '') !== (p.name || '')) { p.name = it.name || ''; changed = true; }
    if (Object.prototype.hasOwnProperty.call(it, 'prompt') && String(it.prompt || '') !== String(p.prompt || '')) {
      p.prompt = String(it.prompt || ''); changed = true;
    }
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
function applyDiscoveredEdits(discovered, config) {
  // 統合項目（ステートマシン＋対となるループエントリ）は schedule/enabled の編集を
  // ループ設定側の実体へ書き戻す合成項目に展開する。プロンプト名は変更しない
  // （表示名の変更は workflow.yaml の name へ書き戻す）。
  const expanded = [];
  for (const it of discovered) {
    expanded.push(it);
    const lp = it._src && it._src.kind === 'statemachine' ? it._src.loop : null;
    if (lp && lp.file) {
      expanded.push({
        name: lp.promptName,
        schedule: it.schedule,
        enabled: it.enabled,
        _src: {
          kind: 'loop', file: lp.file, format: lp.format, repo: it._src.repo,
          promptIndex: lp.promptIndex, promptName: lp.promptName, scheduleKey: lp.scheduleKey,
        },
      });
    }
  }
  const byFile = new Map();
  for (const it of expanded) {
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

    if (first.kind === 'loop') {
      if (first.format === 'json') {
        const r = applyKiroLoopJson(raw, items);
        errors.push(...r.errors);
        if (r.changed) newText = r.text;
      } else {
        const current = parseAgentLoopPrompts(raw.replace(/\r\n/g, '\n'));
        const currentTexts = agentLoopPromptTexts(raw.replace(/\r\n/g, '\n'));
        const edits = [];
        for (const it of items) {
          const cur = current[it._src.promptIndex] || {};
          const edit = { promptIndex: it._src.promptIndex, promptName: it._src.promptName, scheduleKey: it._src.scheduleKey };
          let changed = false;
          if ((it.name || '') !== (cur.name || '')) { edit.name = it.name || ''; changed = true; }
          if ((it.enabled !== false) !== (cur.enabled !== false)) { edit.enabled = it.enabled !== false; changed = true; }
          if (Object.prototype.hasOwnProperty.call(it, 'prompt')
              && String(it.prompt || '') !== String(currentTexts[it._src.promptIndex] || '')) {
            edit.prompt = String(it.prompt || ''); changed = true;
          }
          if (it._src.scheduleKey && (it.schedule || '') !== scheduleOf(cur).schedule) {
            edit.schedule = it.schedule || '';
            changed = true;
          }
          if (changed) edits.push(edit);
        }
        if (edits.length) {
          const r = applyAgentLoopEdits(raw, edits);
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
        touched.push({ repo: first.repo, relFiles: [relPosix(first.repo, file, config)] });
      } catch {
        errors.push(`書き込み失敗: ${file}`);
      }
    }
  }
  return { touched, errors };
}

// 手動項目を config へ保存する際、実行時フィールドを落とす。
// executionChoice は残す（業務ごとの実行エージェント設定＝人の選択の保存場所）。
function stripRuntimeFields(it) {
  const {
    state, _src, source, enabled, command,
    parameters, parameterError, execution, executionSource, autoExecution,
    ...rest
  } = it;
  const choice = normalizeExecutionChoice(rest.executionChoice);
  if (choice) rest.executionChoice = choice;
  else delete rest.executionChoice;
  return rest;
}

function applyManagedItems(items, config) {
  const byRepo = new Map();
  for (const item of items.filter((it) => it.managed && it.repo)) {
    const key = _pathKey(item.repo);
    if (!byRepo.has(key)) byRepo.set(key, { repo: item.repo, items: [] });
    byRepo.get(key).items.push(item);
  }
  const touched = [];
  const errors = [];
  for (const { repo, items: repoItems } of byRepo.values()) {
    const root = viewerRepo(repo, config) || repo;
    const { file, format } = loopConfigFile(root);
    if (format === 'json') {
      // upsert は YAML テキストの外科的書換で、JSON へ流し込むと壊す。既存の .json を
      // 避けて .yml を作ると agent-loop の探索順（yaml → yml → json）で .json が
      // 無視され、元のプロンプトが黙って消えたように見える。どちらも取らず断る。
      errors.push(`定常業務の設定が JSON（${path.basename(file)}）のため画面から編集できません。`
        + ' YAML へ移すか、ファイルを直接編集してください。');
      continue;
    }
    let raw;
    try { raw = fs.readFileSync(file, 'utf8'); } catch { raw = 'prompts:\n'; }
    let next = raw;
    for (const item of repoItems) {
      const prompt = item.type === 'state-machine'
        ? (item.schedule ? `statemachine-use スキルで${item.workflow || item.id}ステートマシンを実行して` : null)
        : String(item.prompt || '').trim();
      next = upsertManagedAgentPrompt(next, item, prompt).text;
    }
    if (next === raw) continue;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, next, 'utf8');
      touched.push({ repo, relFiles: [relPosix(repo, file, config)] });
    } catch (err) {
      errors.push(`定常業務の設定ファイル（${path.basename(file)}）の書き込み失敗: ${err.message}`);
    }
  }
  return { touched, errors };
}

// ---------------------------------------------------------------------------
// 定常業務ワークスペースの登録（S2）
// ---------------------------------------------------------------------------

// フォルダを見て「定常業務として登録する価値があるか」を返す（登録前のプレビュー用）。
// マーカーが無ければ registered=false で返し、画面は「ステートマシン新規作成」へ誘導する。
function inspectCoworkRoot(config, dir) {
  const raw = String(dir || '').trim();
  if (!raw) throw new Error('フォルダを選択してください');
  const folder = _resolveRoot(raw, config);
  if (!isDir(folder)) throw new Error(`フォルダがありません: ${folder}`);
  const roots = ((config && config.cowork) || {}).roots || [];
  const registered = roots.some((r) => _pathKey(_resolveRoot(String(r || ''), config)) === _pathKey(folder));
  // 直下だけでなく既定の走査深さまで見る（登録するのは親フォルダで、実体は 1 段下、という
  // 置き方が普通にある。ここで「マーカー無し」と言い切ると登録できるのに拒否してしまう）。
  const depth = Math.max(1, Number(((config && config.cowork) || {}).scanDepth || 2));
  const found = scanForCoworkConfigs(folder, depth);
  return {
    folder,
    registered,
    // 実行エンジンが既に担当しているプロジェクトなら、登録しなくても定常業務は見えている。
    managedByEngine: require('../../agent-project/main/engine')
      .projectRoots(config)
      .some((r) => _pathKey(_resolveRoot(String(r || ''), config)) === _pathKey(folder)),
    markers: found.map((m) => ({
      folder: m.folder,
      loop: m.kiroFile ? path.basename(m.kiroFile) : '',
      stateMachines: m.smNames || [],
    })),
  };
}

// 登録 / 登録解除。設定 `cowork.roots` だけを触る（実体ファイルには何も書かない）。
function setCoworkRoot(config, saveConfig, { dir, remove } = {}) {
  const folder = _resolveRoot(String(dir || '').trim(), config);
  if (!folder) throw new Error('フォルダを選択してください');
  const cfg = { ...(config || {}) };
  const cur = ((cfg.cowork || {}).roots || []).map((r) => String(r || '').trim()).filter(Boolean);
  const key = _pathKey(folder);
  const kept = cur.filter((r) => _pathKey(_resolveRoot(r, config)) !== key);
  if (!remove) {
    if (!isDir(folder)) throw new Error(`フォルダがありません: ${folder}`);
    kept.push(folder);
  }
  cfg.cowork = { ...(cfg.cowork || {}), roots: kept };
  saveConfig(cfg);
  return { ok: true, roots: kept, folder, removed: !!remove };
}


function saveWork(config, saveConfig, { items, branch, createBranch, push } = {}) {
  const all = Array.isArray(items) ? items : [];
  const configItems = all.filter((it) => it.source !== 'discovered');
  const discovered = all.filter((it) => it.source === 'discovered' && it._src);

  // 1) 手動項目のみ dashboard 設定へ保存（発見項目は実体ファイルが正）。
  //    発見項目の実行エージェント設定だけは実体ファイルへ書かず、対応表として dashboard
  //    設定に持つ（agent-loop / statemachine の契約に dashboard 語彙を持ち込まない）。
  const cfg = { ...(config || {}) };
  const executionChoices = {};
  for (const it of discovered) {
    const choice = normalizeExecutionChoice(it.executionChoice);
    if (choice && it.id) executionChoices[String(it.id)] = choice;
  }
  cfg.cowork = {
    ...(cfg.cowork || {}),
    items: configItems.map(stripRuntimeFields),
    executionChoices,
  };
  const saved = saveConfig(cfg);

  // 2) 発見項目の編集を実体ファイルへ書き戻し
  const wb = applyDiscoveredEdits(discovered, config);
  const managed = applyManagedItems(configItems, config);

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
  for (const t of managed.touched) {
    const e = ensure(t.repo);
    if (e) t.relFiles.forEach((f) => e.relFiles.add(f));
  }
  for (const it of configItems) ensure(it.repo);

  const git = [...repoMap.values()].map(({ repo, relFiles }) => {
    const files = [...relFiles];
    const commit = gitCommitFiles(repo, files, 'chore(cowork): update agent-loop/statemachine config', config);
    const save = gitSave(repo, { branch, createBranch, push }, config);
    return { repo, result: { ...save, commit } };
  });
  invalidateDiscoverCache();
  return { config: saved, git, writeback: { errors: [...wb.errors, ...managed.errors] } };
}

module.exports = {
  overview, runLoop, runStateMachine, generateStateMachine, runAdhoc, adhocRoots,
  saveWork, itemsOf, wslPath, dynamicState,
  resolveItem, findItem, dedupeItems, applyDiscoveredEdits, gitCommitFiles,
  invalidateDiscoverCache, decodeCliOutput, viewerRepo,
  itemLogs, readLog, appendHistory, readHistory, historyFile,
  resolveLoopPromptText, withGlobalInstructions, planSessionCommands,
  coworkChatLaunch, routineLaunchPlan, resolveRoutineAgent, routineTierOverview,
  normalizeExecutionChoice, storedExecutionChoice,
  ROUTINE_WORKLOAD, LEGACY_CHAT_COMMAND,
  applyManagedItems, stateMachineCreationPrompt,
  inspectCoworkRoot, setCoworkRoot,
  templateParameterKeys, stateMachineInputSpec, stateMachineFilePath, resolveLoopAcceptance,
  needsHeadlessHarness,
  routineParameterSpec, validateParameters, applyParameters, stateMachineParameterBlock,
  stateMachineHarnessArgs, harnessWorkflowArg,
};
