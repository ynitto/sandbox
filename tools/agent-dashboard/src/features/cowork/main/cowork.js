'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const {
  makeLoopProvider, isWslPath, wslPath, wslDistro, toWslCwd, shellQuote, decodeCliOutput,
} = require('./loopProvider');
const { _pathKey, _isPosixAbs, toViewerPath } = require('../../agent-project/main/project');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');
const {
  discoverCoworkItems, parseKiroLoopPrompts, scheduleOf, detectMarkers, kiroLoopPromptTexts,
} = require('./discover');
const { applyKiroLoopEdits, applyStatemachineEdits } = require('./writeback');

// 発見結果キャッシュ。overview のポーリングごとに roots を再走査しない。
const DISCOVER_TTL_MS = 30000;
let _discoverCache = { key: '', at: 0, items: null };

function discoverCacheKey(config) {
  const roots = ((config && config.projects && config.projects.roots) || []).map(String).join('\0');
  const cw = (config && config.cowork) || {};
  const depth = cw.scanDepth || (config && config.projects && config.projects.scanDepth) || 2;
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
function viewerRepo(repo) {
  const s = String(repo || '');
  if (!s) return '';
  if (process.platform === 'win32' && _isPosixAbs(s)) return toViewerPath(s);
  return s;
}

function listLogCandidates(repo, type) {
  const root = viewerRepo(repo);
  if (!root) return [];
  const names = type === 'loop'
    ? ['.kiro-loop/logs', '.agent-loop/logs', 'logs']
    : ['.statemachine-use/logs', 'logs'];
  const out = [];
  for (const n of names) {
    const dir = path.join(root, n);
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
  const logs = listLogCandidates(repo, type).map((l) => {
    let size = 0;
    try { size = fs.statSync(l.file).size; } catch { /* 消えた候補 */ }
    return { file: l.file, name: path.basename(l.file), mtimeMs: l.mtimeMs, size };
  });
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
  const candidates = listLogCandidates(item.repo || item.cwd || '', type);
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
  const command = item.type === 'state-machine' ? (cfg.stateMachineCommand || 'statemachine-use') : (cfg.loopCommand || cfg.loopProvider || 'kiro-loop');
  if (process.platform === 'win32') {
    // kiro-loop は WSL 側で動く想定なので、リポジトリが Windows ドライブ上でも WSL を探査する。
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
function dynamicState(item, cfg, { probeProcess = false } = {}) {
  const repo = item.repo || item.cwd || '';
  const proc = probeProcess ? processStatus(item, cfg) : { running: false, detail: '' };
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
    probed: !!probeProcess,
  };
}

function normalizeItem(item, i, cfg, stateOpts) {
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
    state: dynamicState({ ...item, id, type }, cfg, stateOpts),
  };
}

// 発見項目（discover.js 由来）を Cowork 項目へ。source/_src/enabled を保持しつつ live state を付与。
function normalizeDiscovered(d, cfg, stateOpts) {
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
    state: dynamicState({ ...d, type }, cfg, stateOpts),
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
  return rawDiscovered(config, opts).map((d) => normalizeDiscovered(d, cfg, opts));
}

// opts.probeProcess: true のときだけプロセス探査（実行直後・手動更新用）。ポーリングはログのみ。
// opts.forceDiscover: true で発見キャッシュを無視して再走査。
function overview(config, opts = {}) {
  const cfg = config.cowork || {};
  const stateOpts = { probeProcess: opts.probeProcess === true };
  const discoverOpts = { forceDiscover: opts.forceDiscover === true, ...stateOpts };
  const loop = makeLoopProvider(cfg);
  const configItems = itemsOf(cfg).map((item, i) => normalizeItem(item, i, cfg, stateOpts));
  const discovered = discoverNormalized(config, cfg, discoverOpts);
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
    return rawDiscovered(config).find((d) => d.id === String(id)) || null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// 実行プロンプトの解決（ウィンドウ実行 = kiro-loop を介さず tmux + kiro-cli へ直接送る用）
// ---------------------------------------------------------------------------

// {{…}} プレースホルダーやステートマシンの入力パラメータなど、ユーザー入力が必須の
// 項目が残っているケースの補助。勝手に仮の値で進めず、まず人へ質問させる。
const INPUT_ASSIST =
  '（補足）このプロンプトに {{…}} のようなプレースホルダーや、ステートマシンの入力'
  + 'パラメータなど、実行に必要な入力がまだ埋まっていない場合は、勝手に仮の値で進めず、'
  + '先に必要な入力を箇条書きで私に質問し、回答を得てから実行してください。'
  + 'すべて埋まっている場合はそのまま実行してください。';

function withInputAssist(prompt) {
  const p = String(prompt || '').trim();
  return p ? `${p}\n\n${INPUT_ASSIST}` : p;
}

// repo の .kiro/kiro-loop.{yaml,yml,json} から定期プロンプト本文を名前で解決する。
// 見つからなければ ''（呼び出し側が代替の指示文へフォールバックする）。
function resolveLoopPromptText(repo, promptName) {
  const root = viewerRepo(repo) || String(repo || '');
  const name = String(promptName || '').trim();
  if (!root || !name) return '';
  let marker = null;
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
  const entries = parseKiroLoopPrompts(norm);
  const texts = kiroLoopPromptTexts(norm);
  // 発見項目の既定名（prompt-<n>）でも引けるようにインデックス名も突き合わせる
  const idx = entries.findIndex((e, i) => (e.name || `prompt-${i + 1}`) === name);
  return idx >= 0 ? String(texts[idx] || '') : '';
}

function runLoop(config, itemIdValue) {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 作業が見つかりません: ${itemIdValue}`);
  // 実行対象は kiro-loop の prompt 名（合成 id ではない）。`send` が cwd の
  // .kiro/kiro-loop.* から名前解決するため、手動項目も表示名を優先する。
  const runId = item.source === 'discovered'
    ? ((item._src && item._src.promptName) || item.name)
    : (item.name || item.id);
  const cwd = viewerRepo(item.repo || item.cwd) || item.repo || item.cwd;
  // ウィンドウ実行用: kiro-loop.yml のプロンプト本文を解決して直接送る（kiro-loop 非経由）。
  // 本文を解決できなければ、エージェント自身に設定を読ませる指示文で代替する。
  // 明示 args の項目は従来の <loopCommand> 実行のまま（prompt を渡さない）。
  const prompt = Array.isArray(item.args)
    ? undefined
    : withInputAssist(
      resolveLoopPromptText(item.repo || item.cwd, runId)
        || `.kiro/kiro-loop.yml（または kiro-loop.yaml / .json）の定期プロンプト「${runId}」の本文を読んで、その指示を実行して`
    );
  const res = makeLoopProvider(cfg).run({ ...item, cwd, id: runId, prompt });
  recordRun(cfg, { ...item, type: 'loop' }, res);
  return res;
}

function runStateMachine(config, itemIdValue, input) {
  const cfg = config.cowork || {};
  const item = resolveItem(config, itemIdValue);
  if (!item) throw new Error(`Cowork 定型業務が見つかりません: ${itemIdValue}`);
  const cwd = viewerRepo(item.repo || item.cwd) || item.repo || item.cwd || process.cwd();
  // statemachine-use は CLI ではなくスキル。エージェントセッションへ
  // 「statemachine-use スキルで xxx ステートマシンを実行して」を送って発動する。
  // kiro-loop.yml に対となる定期プロンプトがある統合項目はその本文を優先する。
  let args;
  let prompt;
  const pairedName = item._src && item._src.loop && item._src.loop.promptName;
  if (Array.isArray(item.args)) {
    args = [...item.args];
    if (input) args.push(String(input));
    // 明示 args はレガシー実行（prompt を渡さない）
  } else {
    const smName = item.workflow || item.file || item.name;
    const pairedBody = pairedName ? resolveLoopPromptText(item.repo || item.cwd, pairedName) : '';
    const smPrompt = (pairedBody && !input)
      ? pairedBody
      : `statemachine-use スキルで${smName}ステートマシンを実行して${input ? `。入力: ${String(input)}` : ''}`;
    prompt = withInputAssist(smPrompt);
    // 非ウィンドウ実行（非 win32 / runWindow:false）用の従来 send 引数も併せて用意する
    const legacy = (pairedName && !input)
      ? pairedName
      : `${smName} ステートマシンを実行して${input ? `。入力: ${String(input)}` : ''}`;
    args = ['send', legacy];
  }
  const res = makeLoopProvider(cfg).run({ ...item, cwd, args, prompt, timeoutMs: item.timeoutMs || 60000 });
  recordRun(cfg, { ...item, type: 'state-machine' }, res);
  return res;
}

function gitInRepo(repo, args, timeoutMs) {
  if (process.platform === 'win32' && isWslPath(repo)) {
    const distro = wslDistro(repo);
    const script = `export LANG=C.UTF-8 LC_ALL=C.UTF-8; cd ${shellQuote(wslPath(repo))} && git ${args.map(shellQuote).join(' ')}`;
    const wslArgs = distro ? ['-d', distro, '-e', 'sh', '-lc', script] : ['-e', 'sh', '-lc', script];
    return sh('wsl.exe', wslArgs, { timeoutMs });
  }
  return sh('git', ['-C', viewerRepo(repo) || repo, ...args], { timeoutMs });
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
  const root = viewerRepo(repo) || repo;
  return path.relative(root, file).split(path.sep).join('/');
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
  // 統合項目（ステートマシン＋対となる kiro-loop エントリ）は schedule/enabled の編集を
  // kiro-loop 側の実体へ書き戻す合成項目に展開する。プロンプト名は変更しない
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
          kind: 'kiro-loop', file: lp.file, format: lp.format, repo: it._src.repo,
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
  invalidateDiscoverCache();
  return { config: saved, git, writeback: { errors: wb.errors } };
}

module.exports = {
  overview, runLoop, runStateMachine, saveWork, itemsOf, wslPath, dynamicState,
  resolveItem, findItem, dedupeItems, applyDiscoveredEdits, gitCommitFiles,
  invalidateDiscoverCache, decodeCliOutput, viewerRepo,
  itemLogs, readLog, appendHistory, readHistory, historyFile,
  resolveLoopPromptText, withInputAssist,
};
