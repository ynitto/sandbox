'use strict';

// 操作の記録（Recording）— 人が画面でやった操作の記録を、工程列（model.js の raw 形）へ
// **決定的に**変換する。変換の規則は agent-dashboard の recording.js と同じ
// （docs/plans/2026-09-04-agent-dashboard-routine-recording-feasibility.md §3.1）:
//   - 要素は role と名前（`getByRole` / `auto_id:=` / `name:=`）で持ち、ref（e15）や座標は捨てる。
//   - 入力した値は `{{key}}` にし、記録時の値は「例」として残す。パスワードらしい欄は例にも残さない。
//   - 工程は goto / ウィンドウの切り替わり / 確定の操作（ボタン・リンク・Enter）で切る。
//   - 推測はしない。待機・確認・分岐は工程の本文（コンパイルが書く案内）と人の編集に任せる。
//
// 読む記録は 2 種類:
//   1. ブラウザ … `playwright-cli recording-start` → 操作 → `recording-stop` が印字する Playwright コード行
//   2. Windows アプリ … `winauto record` が書く操作イベント（JSONL。契約は WINAUTO_EVENT_KINDS）
//
// 記録の開始・終了は、この端末の PATH にある `playwright-cli` / `winauto` を **直接** 呼ぶ
// （WSL との橋渡しはしない）。呼ぶ関数は引数で受け取るので、ここは Electron に触れない。

const OPS = ['goto', 'launch', 'click', 'dblclick', 'fill', 'type', 'press', 'select', 'check', 'uncheck', 'hover', 'keys', 'window'];
const COMMIT_OPS = new Set(['click', 'dblclick', 'press']);
const COMMIT_ROLES = new Set(['button', 'link', 'menuitem', 'tab', 'Button', 'MenuItem', 'Hyperlink', 'TabItem']);
const VALUE_OPS = new Set(['fill', 'type']);
const SECRET_RE = /pass(word)?|パスワード|暗証|secret|token|トークン|pin\b|otp|認証コード/i;
const SHELL_META_RE = /[|&;<>`\n]|\$\(/;
const MAX_OPS = 200;
const MAX_STEP_OPS = 12;
const MAX_TEXT = 300;
const WINAUTO_EVENT_KINDS = ['invoke', 'value', 'select', 'toggle', 'keys', 'window', 'launch'];

const ROLE_JA = {
  button: 'ボタン', link: 'リンク', textbox: '入力欄', combobox: '選択欄', checkbox: 'チェック', radio: 'ラジオ',
  menuitem: 'メニュー', tab: 'タブ', option: '選択肢', listbox: 'リスト', searchbox: '検索欄', spinbutton: '数値欄',
  Button: 'ボタン', Edit: '入力欄', ComboBox: '選択欄', CheckBox: 'チェック', RadioButton: 'ラジオ', MenuItem: 'メニュー',
  Hyperlink: 'リンク', TabItem: 'タブ', ListItem: '項目', Text: 'テキスト', Window: 'ウィンドウ',
};

function text(value, max = MAX_TEXT) {
  return String(value == null ? '' : value).replace(/\r\n/g, '\n').trim().slice(0, max);
}

// --- Playwright コード行 ------------------------------------------------------------

function readStringLiteral(src, from) {
  const quote = src[from];
  if (quote !== '\'' && quote !== '"' && quote !== '`') return null;
  let out = '';
  for (let i = from + 1; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '\\' && i + 1 < src.length) {
      const next = src[i + 1];
      out += next === 'n' ? '\n' : next === 't' ? '\t' : next;
      i += 1;
      continue;
    }
    if (ch === quote) return { value: out, end: i + 1 };
    out += ch;
  }
  return null;
}

function stringLiterals(src) {
  const out = [];
  for (let i = 0; i < src.length; i += 1) {
    const lit = readStringLiteral(src, i);
    if (lit) { out.push(lit.value); i = lit.end - 1; }
  }
  return out;
}

function describeLocator(expr) {
  const m = /^(getByRole|getByLabel|getByText|getByPlaceholder|getByTestId|getByTitle|getByAltText|locator)\(([\s\S]*)\)$/.exec(expr);
  if (!m) return { role: '', label: expr.slice(0, 80) };
  const lits = stringLiterals(m[2]);
  if (m[1] === 'getByRole') return { role: lits[0] || '', label: lits[1] || '' };
  return { role: '', label: lits[0] || '' };
}

const PW_METHODS = new Set(['click', 'dblclick', 'fill', 'type', 'press', 'check', 'uncheck', 'selectOption', 'hover', 'pressSequentially']);
const PW_OP = { selectOption: 'select', pressSequentially: 'type' };

function parsePlaywrightLine(line) {
  const src = String(line || '').trim().replace(/;$/, '');
  let m = /^await\s+page\.goto\((.*)\)$/.exec(src);
  if (m) {
    const url = stringLiterals(m[1])[0] || '';
    return url ? { op: 'goto', target: url, label: url, value: '' } : null;
  }
  m = /^await\s+page\.keyboard\.(press|type)\((.*)\)$/.exec(src);
  if (m) {
    const value = stringLiterals(m[2])[0] || '';
    return { op: m[1] === 'press' ? 'press' : 'type', target: '', label: '', value };
  }
  m = /^await\s+page\.((?:getBy\w+|locator)\([\s\S]*\)(?:\.(?:first|last|nth\(\d+\)|filter\([\s\S]*?\))\(?\)?)*)\.(\w+)\(([\s\S]*)\)$/.exec(src);
  if (!m || !PW_METHODS.has(m[2])) return null;
  const { role, label } = describeLocator(m[1]);
  const op = PW_OP[m[2]] || m[2];
  const args = stringLiterals(m[3]);
  const value = ['click', 'dblclick', 'check', 'uncheck', 'hover'].includes(op) ? '' : (args[0] || '');
  return { op, target: m[1], role, label, value };
}

function parsePlaywrightRecording(raw) {
  const src = String(raw || '');
  const fence = /```(?:js|ts|javascript|typescript)?\n([\s\S]*?)```/.exec(src);
  const body = fence ? fence[1] : src;
  const ops = [];
  for (const line of body.split(/\r?\n/)) {
    const op = parsePlaywrightLine(line);
    if (op) ops.push(op);
  }
  return ops;
}

// --- winauto イベント（JSONL） ------------------------------------------------------

function winautoSelector(ev) {
  const autoId = text(ev.auto_id, 120);
  const name = text(ev.name, 120);
  const control = text(ev.control_type, 40);
  const parts = [];
  if (autoId) parts.push(`auto_id:=${autoId}`);
  else if (name) parts.push(`name:=${name}`);
  if (control && !autoId) parts.push(`control:=${control}`);
  return parts.join(' >> ');
}

function parseWinautoEvent(line) {
  const src = String(line || '').trim();
  if (!src || src.startsWith('#')) return null;
  let ev;
  try { ev = JSON.parse(src); } catch { return null; }
  if (!ev || typeof ev !== 'object') return null;
  const kind = String(ev.event || '');
  if (!WINAUTO_EVENT_KINDS.includes(kind)) return null;
  const app = text(ev.app, 120);
  const window = text(ev.window, 120);
  const base = { app, window, role: text(ev.control_type, 40), label: text(ev.name, 120) };
  if (kind === 'launch') return { ...base, op: 'launch', target: text(ev.path || ev.app, 200), value: '' };
  if (kind === 'window') return { ...base, op: 'window', target: window, label: window, value: '' };
  if (kind === 'keys') return { ...base, op: 'keys', target: winautoSelector(ev), value: text(ev.value, 120) };
  const target = winautoSelector(ev);
  if (!target) return null;
  if (kind === 'invoke') return { ...base, op: 'click', target, value: '' };
  if (kind === 'value') return { ...base, op: 'fill', target, value: text(ev.value, MAX_TEXT) };
  if (kind === 'select') return { ...base, op: 'select', target, value: text(ev.value, 120) };
  const on = String(ev.value == null ? 'on' : ev.value).toLowerCase();
  return { ...base, op: on === 'off' || on === 'false' || on === '0' ? 'uncheck' : 'check', target, value: '' };
}

function parseWinautoRecording(raw) {
  const ops = [];
  for (const line of String(raw || '').split(/\r?\n/)) {
    const op = parseWinautoEvent(line);
    if (op) ops.push(op);
  }
  return ops;
}

// --- 整理 ---------------------------------------------------------------------------

function parameterKey(label, index, used) {
  const ascii = String(label || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  let key = ascii && /^[a-z]/.test(ascii) ? ascii.slice(0, 30) : `input_${index}`;
  let n = 2;
  while (used.has(key)) { key = `${key.replace(/_\d+$/, '')}_${n}`; n += 1; }
  used.add(key);
  return key;
}

function dedupe(ops) {
  const out = [];
  for (const op of ops) {
    const prev = out[out.length - 1];
    if (prev && prev.op === op.op && prev.target === op.target && prev.value === op.value) continue;
    if (prev && prev.op === 'click' && op.op === 'press' && /^(enter|return)$/i.test(op.value || '') && prev.target === op.target) continue;
    if (prev && prev.op === 'click' && VALUE_OPS.has(op.op) && prev.target === op.target) out.pop();
    out.push(op);
  }
  return out;
}

function parameterize(ops) {
  const used = new Set();
  let n = 0;
  return ops.map((op) => {
    if (!VALUE_OPS.has(op.op) || !op.value) return { ...op, param: '', example: '' };
    n += 1;
    const secret = SECRET_RE.test(op.label || '') || SECRET_RE.test(op.target || '');
    const key = parameterKey(op.label, n, used);
    return { ...op, param: key, example: secret ? '' : op.value, value: `{{${key}}}` };
  });
}

function isCommit(op) {
  if (op.op === 'press') return /^(enter|return)$/i.test(op.value || '');
  return COMMIT_OPS.has(op.op) && (!op.role || COMMIT_ROLES.has(op.role));
}

function segment(ops) {
  const groups = [];
  let cur = null;
  const open = (seed) => { cur = { context: seed, ops: [] }; groups.push(cur); };
  for (const op of ops) {
    if (op.op === 'goto' || op.op === 'launch' || op.op === 'window') { open(op); continue; }
    if (!cur || cur.closed || cur.ops.length >= MAX_STEP_OPS) open(cur ? { ...cur.context, inherited: true } : null);
    cur.ops.push(op);
    if (isCommit(op)) cur.closed = true;
  }
  return groups.filter((g) => g.ops.length || (g.context && !g.context.inherited));
}

function quoteLabel(op) {
  const label = op.label || op.target || '';
  const kind = ROLE_JA[op.role] || '';
  return kind ? `「${label}」${kind}` : `「${label}」`;
}

function describeOp(op) {
  switch (op.op) {
    case 'goto': return `${op.target} を開く`;
    case 'launch': return `${op.target} を起動する`;
    case 'window': return `「${op.target}」ウィンドウが前面になる`;
    case 'click': return `${quoteLabel(op)}を押す`;
    case 'dblclick': return `${quoteLabel(op)}をダブルクリックする`;
    case 'hover': return `${quoteLabel(op)}にカーソルを合わせる`;
    case 'fill':
    case 'type': {
      const ex = op.example ? `（記録時の例: ${op.example}）` : '';
      return op.label || op.target ? `${quoteLabel(op)}に ${op.value} を入力する${ex}` : `${op.value} を入力する${ex}`;
    }
    case 'press': return `${op.value} キーを押す`;
    case 'keys': return `キー操作 ${op.value} を送る`;
    case 'select': return `${quoteLabel(op)}で「${op.value}」を選ぶ`;
    case 'check': return `${quoteLabel(op)}にチェックを入れる`;
    case 'uncheck': return `${quoteLabel(op)}のチェックを外す`;
    default: return '';
  }
}

function stepTitle(group) {
  const commit = [...group.ops].reverse().find(isCommit);
  const op = commit || group.ops[0] || group.context;
  if (!op) return '';
  if (op.op === 'press') return 'Enter で確定する';
  if (op.op === 'goto') return '画面を開く';
  if (op.op === 'launch' || op.op === 'window') return `${op.target} を開く`;
  return describeOp(op).replace(/（記録時の例: .*）$/, '').slice(0, 60);
}

function recordedEntry(op) {
  const out = { op: op.op, target: text(op.target, MAX_TEXT), label: text(op.label, 120) };
  if (op.role) out.role = text(op.role, 40);
  if (op.value) out.value = text(op.value, MAX_TEXT);
  if (op.example) out.example = text(op.example, 120);
  return out;
}

// 確認コマンドは**この工程がもたらした変化**を測る。見るのは「次の工程が始まったときのウィンドウ」。
function checkFor(kind, nextGroup, app) {
  if (kind !== 'windows' || !app) return '';
  const ctx = nextGroup && nextGroup.context;
  const opened = ctx && ctx.op === 'window' ? text(ctx.target, 120) : '';
  if (!opened || SHELL_META_RE.test(opened) || SHELL_META_RE.test(app)) return '';
  return `winauto wait name:=${opened} --app ${app}`;
}

function stepsFromOps(kind, ops, { url = '', app = '' } = {}) {
  const cleaned = parameterize(dedupe(ops.slice(0, MAX_OPS)));
  const groups = segment(cleaned);
  let currentUrl = url;
  let currentApp = app;
  const steps = [];
  for (const [index, group] of groups.entries()) {
    const ctx = group.context;
    if (ctx && ctx.op === 'goto') currentUrl = ctx.target;
    if (ctx && (ctx.op === 'launch' || ctx.op === 'window') && ctx.app) currentApp = ctx.app;
    const lines = [];
    const recorded = [];
    if (ctx && !ctx.inherited) { lines.push(describeOp(ctx)); recorded.push(recordedEntry(ctx)); }
    for (const op of group.ops) { lines.push(describeOp(op)); recorded.push(recordedEntry(op)); }
    steps.push({
      kind,
      title: stepTitle(group),
      target: kind === 'browser' ? currentUrl : currentApp,
      detail: lines.map((l, i) => `${i + 1}. ${l}`).join('\n'),
      check: checkFor(kind, groups[index + 1], currentApp),
      checkRetries: 1,
      outcomes: [],
      recorded,
    });
  }
  const parameters = [...new Set(cleaned.map((o) => o.param).filter(Boolean))];
  return { steps, parameters, operations: cleaned.length };
}

// 入口。source は 'browser'（playwright-cli のコード行）か 'windows'（winauto の JSONL）。
function stepsFromRecording({ source, text: raw, url = '', app = '' } = {}) {
  const kind = String(source || '').trim();
  if (kind !== 'browser' && kind !== 'windows') throw new Error('記録の種類はブラウザか Windows アプリのどちらかです');
  const ops = kind === 'browser' ? parsePlaywrightRecording(raw) : parseWinautoRecording(raw);
  if (!ops.length) {
    throw new Error(kind === 'browser'
      ? '記録から操作を読み取れませんでした（playwright-cli recording-stop の出力を貼り付けてください）'
      : '記録から操作を読み取れませんでした（winauto の操作イベント JSONL を貼り付けてください）');
  }
  return { source: kind, ...stepsFromOps(kind, ops, { url: text(url, 500), app: text(app, 120) }) };
}

// --- ブラウザの記録（playwright-cli を直接呼ぶ） --------------------------------------

const RECORD_SESSION = 'statemachine-maker-record';
const PLAYWRIGHT_CLI = 'playwright-cli';
const PLAYWRIGHT_RECORDING_COMMANDS = ['recording-start', 'recording-stop'];

function playwrightArgs(...rest) {
  return [`-s=${RECORD_SESSION}`, ...rest];
}

// --version が成功しても、古い playwright-cli には操作記録のコマンドが無い。
// コマンド一覧そのものを見て、開始と終了が揃っていることを確かめる。
function supportsPlaywrightRecording(raw) {
  const body = String(raw || '');
  return PLAYWRIGHT_RECORDING_COMMANDS.every((name) => new RegExp(`^\\s*${name}\\b`, 'm').test(body));
}

// 失敗の理由が書かれている行を拾う。CLI は先頭に見出しや空行を置き、原因はその下に来る
// ことがある（最初の 1 行だけ見せると「### Error」で終わってしまう）。
function firstLine(res) {
  const lines = String((res && (res.stderr || res.stdout)) || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const meaningful = lines.find((l) => /error|not found|cannot|enoent|failed|見つかり/i.test(l) && !/^#+\s*Error$/i.test(l));
  return String(meaningful || lines[0] || '').slice(0, 200);
}

function browserOpenHint(detail) {
  const body = String(detail || '');
  if (/XServer|X server|\$?DISPLAY|xvfb|Wayland/i.test(body)) return '（画面の無い環境ではブラウザを開けません。別のパソコンで取った記録を貼り付けてください）';
  // 記録に使うブラウザは既定で Chrome。入っていない環境が一番よく詰まる。
  if (/distribution|is not found at|Executable doesn't exist|install/i.test(body)) {
    return '（記録に使うブラウザが見つかりません。既定は Chrome です。'
      + '`playwright-cli install-browser chrome` を実行するか、Chrome を入れてください）';
  }
  if (/ENOENT|not found|見つかりません/i.test(body)) return '（playwright-cli をこの端末から呼べません。`npm install -g @playwright/cli` を実行し、設定の「準備の確認」で確かめてください）';
  return '';
}

// `capture(command, args, { cwd, timeoutMs })` → { ok, status, stdout, stderr, error }
async function recordBrowserStart({ cwd = '', url = '', capture, timeoutMs = 60000 } = {}) {
  if (typeof capture !== 'function') throw new Error('記録に使う実行関数がありません');
  const target = text(url, 500);
  const capabilities = await capture(PLAYWRIGHT_CLI, ['--help'], { cwd, timeoutMs });
  if (!capabilities || !capabilities.ok) {
    const detail = (capabilities && capabilities.error) || firstLine(capabilities) || PLAYWRIGHT_CLI;
    throw new Error(`playwright-cli の機能を確認できませんでした: ${detail}${browserOpenHint(`${detail} ${(capabilities && capabilities.stderr) || ''}`)}`);
  }
  if (!supportsPlaywrightRecording(`${capabilities.stdout || ''}\n${capabilities.stderr || ''}`)) {
    throw new Error('この playwright-cli はブラウザの操作記録に対応していません。'
      + '`npm install -g @playwright/cli@latest` で更新してから、もう一度「準備の確認」を実行してください');
  }
  const opened = await capture(PLAYWRIGHT_CLI, playwrightArgs('open', '--headed', ...(target ? [target] : [])), { cwd, timeoutMs });
  if (!opened || !opened.ok) {
    const detail = (opened && opened.error) || firstLine(opened) || PLAYWRIGHT_CLI;
    throw new Error(`ブラウザを開けませんでした: ${detail}${browserOpenHint(`${detail} ${(opened && opened.stderr) || ''}`)}`);
  }
  const started = await capture(PLAYWRIGHT_CLI, playwrightArgs('recording-start'), { cwd, timeoutMs });
  if (!started || !started.ok) throw new Error(`記録を開始できませんでした: ${(started && started.error) || firstLine(started)}`);
  return { ok: true, source: 'browser', session: RECORD_SESSION, url: target };
}

async function recordBrowserStop({ cwd = '', url = '', capture, timeoutMs = 60000 } = {}) {
  if (typeof capture !== 'function') throw new Error('記録に使う実行関数がありません');
  const stopped = await capture(PLAYWRIGHT_CLI, playwrightArgs('recording-stop'), { cwd, timeoutMs });
  await capture(PLAYWRIGHT_CLI, playwrightArgs('close'), { cwd, timeoutMs });
  if (!stopped || !stopped.ok) throw new Error(`記録を終了できませんでした: ${(stopped && stopped.error) || firstLine(stopped)}`);
  const raw = String(stopped.stdout || '');
  if (/No actions were recorded/i.test(raw)) throw new Error('操作が記録されていません（ブラウザで操作してから終了してください）');
  return stepsFromRecording({ source: 'browser', text: raw, url });
}

// --- Windows アプリの記録（winauto record を子プロセスで走らせる） ------------------------
//
// `winauto record --app <名前> --output <jsonl> --stop-file <stop>` を起こし、終了は停止ファイルで
// 合図する（record は Ctrl+C か停止ファイルで止まる）。子プロセスの stderr は進行の表示に使う。
// `spawnRecorder({ command, args, cwd })` → { wait(): Promise<{ code, stderr }> } を引数で受ける。

let activeWindows = null;

function windowsRecordingState() {
  return activeWindows ? { app: activeWindows.app, output: activeWindows.out } : null;
}

function resetWindowsRecording() {
  activeWindows = null;
}

async function recordWindowsStart({ cwd = '', app = '', tmpDir = '', fsImpl = require('fs'), pathImpl = require('path'), spawnRecorder } = {}) {
  if (typeof spawnRecorder !== 'function') throw new Error('記録に使う実行関数がありません');
  const name = text(app, 120);
  if (!name) throw new Error('記録するアプリ（ウィンドウ名・プロセス名・PID）を入力してください');
  if (activeWindows) throw new Error('すでに記録中です。先に「記録を終了して工程に起こす」を押してください');
  const dir = tmpDir || pathImpl.join(require('os').tmpdir(), 'statemachine-maker');
  fsImpl.mkdirSync(dir, { recursive: true });
  const key = Date.now().toString(36);
  const out = pathImpl.join(dir, `record-${key}.jsonl`);
  const stop = pathImpl.join(dir, `record-${key}.stop`);
  const reserved = { app: name, out, stop, fsImpl, child: null };
  activeWindows = reserved;
  try {
    reserved.child = spawnRecorder({ command: 'winauto', args: ['record', '--app', name, '--output', out, '--stop-file', stop], cwd });
  } catch (err) {
    if (activeWindows === reserved) activeWindows = null;
    throw new Error(`記録を開始できませんでした: ${(err && err.message) || err}`, { cause: err });
  }
  return { ok: true, source: 'windows', app: name, output: out };
}

async function recordWindowsStop({ timeoutMs = 15000 } = {}) {
  const rec = activeWindows;
  if (!rec) throw new Error('記録が始まっていません（「記録を開始」から始めてください）');
  activeWindows = null;
  rec.fsImpl.writeFileSync(rec.stop, '');
  let exit = { code: null, stderr: '' };
  if (rec.child && typeof rec.child.wait === 'function') {
    exit = await Promise.race([
      rec.child.wait(),
      new Promise((resolve) => { setTimeout(() => resolve({ code: null, stderr: '記録の終了を待ちきれませんでした' }), timeoutMs); }),
    ]);
  }
  let raw;
  try { raw = rec.fsImpl.readFileSync(rec.out, 'utf8'); } catch { raw = ''; }
  for (const f of [rec.out, rec.stop]) { try { rec.fsImpl.unlinkSync(f); } catch { /* 残っても害は無い */ } }
  if (!raw.trim()) {
    const why = (exit.stderr || '').split(/\r?\n/).filter(Boolean).slice(-2).join(' / ');
    throw new Error(`記録が空です${why ? `（${why}）` : ''}。winauto が動く Windows 上で記録してください`);
  }
  return stepsFromRecording({ source: 'windows', text: raw, app: rec.app });
}

module.exports = {
  OPS,
  WINAUTO_EVENT_KINDS,
  RECORD_SESSION,
  supportsPlaywrightRecording,
  parsePlaywrightLine,
  parsePlaywrightRecording,
  parseWinautoEvent,
  parseWinautoRecording,
  describeOp,
  stepsFromOps,
  stepsFromRecording,
  browserOpenHint,
  recordBrowserStart,
  recordBrowserStop,
  recordWindowsStart,
  recordWindowsStop,
  windowsRecordingState,
  resetWindowsRecording,
};
