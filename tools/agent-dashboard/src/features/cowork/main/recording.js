'use strict';

// 操作の記録（Recording）— 人が画面でやった操作の記録を、手順ビルダーの工程列（正規形）へ
// **決定的に**変換する。「人が操作を見せる → 画面が工程に起こす → 作成モードの AI が待機・
// 確認・分岐を補って定義にする」の、真ん中の段だけを担う。
//
// 読む記録は 2 種類で、どちらも実行系の道具が吐くものをそのまま受ける（画面が新しい記録系を
// 持たない）:
//   1. ブラウザ … `playwright-cli recording-start` → 人が操作 → `recording-stop` が印字する
//      Playwright コード行（`await page.getByRole('button', { name: 'ログイン' }).click();`）。
//      CLI 自身の操作（`playwright-cli click e5` 等）も同じ形のコードを印字するので同じ読み方で通る。
//   2. Windows アプリ … `winauto record` が書く操作イベント（JSONL。1 行 1 イベント）。
//      契約は `WINAUTO_EVENT_KINDS`（winauto 側の `RECORD_EVENT_KINDS` と両端で固定してある）。
//
// 変換の方針:
//   - 要素は role と名前（`getByRole` / `auto_id:=` / `name:=`）で持ち、ref（`e15`）や座標は捨てる。
//     記録の ref は 1 回のスナップショット限りで、再現時には別の番号になる。
//   - 入力した値は入力パラメータ `{{key}}` の候補にし、記録時の値は「例」として残す。
//     パスワードらしい欄の値は例にも残さない（秘密情報を項目・指示文に書かない）。
//   - 工程の区切りは決定的に置く。ページ遷移（goto）／ウィンドウの切り替わりで必ず切り、
//     ボタン・リンクを押す／Enter を送る「確定の操作」でも切る（1 工程 = 入力の束 + 確定で、
//     確定の後に `check` を差し込める形になる）。
//   - 推測はしない。待機・確認・分岐・想定外の画面への対処は作成モードの AI に任せ、
//     そのための案内を指示文に載せる（procedure.js の RECORDED_GUIDANCE）。
//
// このモジュールは procedure.js の正規形（工程の raw 形）を組み立てて返すだけで、Electron・
// ファイルシステム・cowork.js に触れない（単体で検査できる）。CLI の起動（記録の開始・終了）は
// `recordBrowser*` が受け取った capture 関数で行い、コマンド綴りの正典もここに置く。

// 記録した操作の種類（工程の `recorded[]` に残す正規形）。
const OPS = ['goto', 'launch', 'click', 'dblclick', 'fill', 'type', 'press', 'select', 'check', 'uncheck', 'hover', 'keys', 'window'];

// 確定の操作。これで工程を切る（後ろに `check` を置ける）。
const COMMIT_OPS = new Set(['click', 'dblclick', 'press']);
const COMMIT_ROLES = new Set(['button', 'link', 'menuitem', 'tab', 'Button', 'MenuItem', 'Hyperlink', 'TabItem']);

// 値をパラメータに倒す操作（人が毎回変える入力）。select / check は選択肢＝定数として残す。
const VALUE_OPS = new Set(['fill', 'type']);

// パスワードらしい欄。値は例にも残さない。
const SECRET_RE = /pass(word)?|パスワード|暗証|secret|token|トークン|pin\b|otp|認証コード/i;

// 確認コマンド（`check`）に載せられない文字。ハーネスはシェルを介さないので、ウィンドウ名や
// アプリ名にシェル記号が混じるときは確認コマンドを作らない（作ると工程列ごと投入前に落ちる）。
const SHELL_META_RE = /[|&;<>`\n]|\$\(/;

const MAX_OPS = 200;
const MAX_STEP_OPS = 12;
const MAX_TEXT = 300;

// winauto の操作イベント（JSONL）の契約。`winauto record --app <名前> --output events.jsonl` が
// 吐く想定の形で、UI Automation のイベント（Invoke / 値の変更 / 選択 / トグル / フォーカスした
// ウィンドウの変化）を 1 行 1 JSON で並べる。
//   {"event":"invoke","app":"勤怠管理","window":"月次集計","control_type":"Button","name":"出力","auto_id":"btnExport"}
//   {"event":"value","app":"勤怠管理","window":"月次集計","control_type":"Edit","name":"対象月","auto_id":"txtMonth","value":"2026-09"}
//   {"event":"select","...","name":"種別","value":"通常"}
//   {"event":"toggle","...","name":"確定済みを含む","value":"on"}
//   {"event":"keys","...","value":"^s"}
//   {"event":"window","app":"勤怠管理","window":"完了"}
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

// --- Playwright コード行の読み取り ------------------------------------------------

// JS の文字列リテラル（'…' / "…"）を先頭から 1 つ読む。エスケープは \' \" \\ \n \t だけ解く。
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

// 引数列から文字列リテラルだけを順に拾う（`{ name: 'x' }` の中の 'x' も拾う。role と name の
// 順は getByRole の書き方で決まっているので、位置で読める）。
function stringLiterals(src) {
  const out = [];
  for (let i = 0; i < src.length; i += 1) {
    const lit = readStringLiteral(src, i);
    if (lit) { out.push(lit.value); i = lit.end - 1; }
  }
  return out;
}

// ロケータ式（`getByRole('textbox', { name: 'ユーザー名' })` 等）から role と表示名を読む。
// 式そのものは `target` として保ち、再現時にそのまま使わせる（ref に戻さない）。
function describeLocator(expr) {
  const m = /^(getByRole|getByLabel|getByText|getByPlaceholder|getByTestId|getByTitle|getByAltText|locator)\(([\s\S]*)\)$/.exec(expr);
  if (!m) return { role: '', label: expr.slice(0, 80) };
  const lits = stringLiterals(m[2]);
  if (m[1] === 'getByRole') return { role: lits[0] || '', label: lits[1] || '' };
  return { role: '', label: lits[0] || '' };
}

const PW_METHODS = new Set(['click', 'dblclick', 'fill', 'type', 'press', 'check', 'uncheck', 'selectOption', 'hover', 'pressSequentially']);
const PW_OP = { selectOption: 'select', pressSequentially: 'type' };

// 1 行の Playwright コードを操作へ。読めない行は null（コメント・空行・assert 等は捨てる）。
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
  const value = op === 'click' || op === 'dblclick' || op === 'check' || op === 'uncheck' || op === 'hover' ? '' : (args[0] || '');
  return { op, target: m[1], role, label, value };
}

// recording-stop の出力（```js … ``` を含む全文でも、コード行だけでもよい）から操作列を読む。
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

// --- winauto イベント（JSONL）の読み取り ------------------------------------------

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

// --- 正規化・整理 -------------------------------------------------------------------

function parameterKey(label, index, used) {
  const ascii = String(label || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  let key = ascii && /^[a-z]/.test(ascii) ? ascii.slice(0, 30) : `input_${index}`;
  let n = 2;
  while (used.has(key)) { key = `${key.replace(/_\d+$/, '')}_${n}`; n += 1; }
  used.add(key);
  return key;
}

// 連続した同じ操作（同じ要素への click の連打など）を 1 回にし、fill の直前の同じ要素への
// click（フォーカスを当てただけ）を落とす。記録の「うまく行った 1 回」から人の癖だけを除く。
function dedupe(ops) {
  const out = [];
  for (const op of ops) {
    const prev = out[out.length - 1];
    if (prev && prev.op === op.op && prev.target === op.target && prev.value === op.value) continue;
    // ボタンを押した直後の同じボタンへの Enter（Playwright の記録が click と一緒に拾う）は確定の重複。
    if (prev && prev.op === 'click' && op.op === 'press' && /^(enter|return)$/i.test(op.value || '') && prev.target === op.target) continue;
    if (prev && prev.op === 'click' && VALUE_OPS.has(op.op) && prev.target === op.target) out.pop();
    out.push(op);
  }
  return out;
}

// 値をパラメータ候補へ。fill / type の値は `{{key}}` に置き、記録時の値は example に残す
// （パスワードらしい欄は example も空）。
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

// 操作列を工程へ切る。goto / launch / window は新しい工程の先頭に置き、確定の操作は工程の末尾に置く。
function segment(ops) {
  const groups = [];
  let cur = null;
  const open = (seed) => { cur = { context: seed, ops: [] }; groups.push(cur); };
  for (const op of ops) {
    if (op.op === 'goto' || op.op === 'launch' || op.op === 'window') {
      open(op);
      continue;
    }
    if (!cur || cur.closed || cur.ops.length >= MAX_STEP_OPS) open(cur ? { ...cur.context, inherited: true } : null);
    cur.ops.push(op);
    if (isCommit(op)) cur.closed = true;
  }
  return groups.filter((g) => g.ops.length || (g.context && !g.context.inherited));
}

function roleJa(role) {
  return ROLE_JA[role] || '';
}

function quoteLabel(op) {
  const label = op.label || op.target || '';
  const kind = roleJa(op.role);
  return kind ? `「${label}」${kind}` : `「${label}」`;
}

// 操作 1 つを人が読める 1 行に。工程の「内容」欄の材料（作成モードの AI も人もこれを読む）。
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

// 工程の名前。確定の操作があればそれ、無ければ先頭の操作から。
function stepTitle(group) {
  const commit = [...group.ops].reverse().find(isCommit);
  const op = commit || group.ops[0] || group.context;
  if (!op) return '';
  if (op.op === 'press') return 'Enter で確定する';
  if (op.op === 'goto') return '画面を開く';
  if (op.op === 'launch' || op.op === 'window') return `${op.target} を開く`;
  return describeOp(op).replace(/（記録時の例: .*）$/, '').slice(0, 60);
}

// 工程に残す記録の形（正規形）。指示文の「記録した操作」節はこれを読む。
function recordedEntry(op) {
  const out = { op: op.op, target: text(op.target, MAX_TEXT), label: text(op.label, 120) };
  if (op.role) out.role = text(op.role, 40);
  if (op.value) out.value = text(op.value, MAX_TEXT);
  if (op.example) out.example = text(op.example, 120);
  return out;
}

// 工程の完了をハーネスが測れる確認コマンド。**測るのはこの工程がもたらした変化**なので、
// 見るのは「次の工程が始まったときのウィンドウ」である（この工程の最中に既に出ていた
// ウィンドウを待っても、押す前から真なので何も検知しない——検知装置が別のものを測るのは
// 検知が無いことより悪い）。次が無い最後の工程には確認を置かない。
// ブラウザには argv で測れる確認が無いので常に空にし、作成モードに読み取りで確かめさせる。
function checkFor(kind, nextGroup, app) {
  if (kind !== 'windows' || !app) return '';
  const ctx = nextGroup && nextGroup.context;
  const opened = ctx && ctx.op === 'window' ? text(ctx.target, 120) : '';
  if (!opened || SHELL_META_RE.test(opened) || SHELL_META_RE.test(app)) return '';
  return `winauto wait name:=${opened} --app ${app}`;
}

// 操作列 → 工程列（procedure.js の raw 形）。kind は 'browser' | 'windows'。
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
    if (ctx && !ctx.inherited) lines.push(describeOp(ctx));
    for (const op of group.ops) lines.push(describeOp(op));
    const recorded = [];
    if (ctx && !ctx.inherited) recorded.push(recordedEntry(ctx));
    for (const op of group.ops) recorded.push(recordedEntry(op));
    steps.push({
      kind,
      title: stepTitle(group),
      target: kind === 'browser' ? currentUrl : currentApp,
      detail: lines.map((l, i) => `${i + 1}. ${l}`).join('\n'),
      check: checkFor(kind, groups[index + 1], currentApp),
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

// --- ブラウザの記録の開始・終了（playwright-cli の呼び出し） ----------------------------

// 記録専用のセッション名。作業のセッション（エージェントが使う既定セッション）と混ぜない。
const RECORD_SESSION = 'agent-dashboard-record';
const PLAYWRIGHT_CLI = 'playwright-cli';

function playwrightArgs(...rest) {
  return [`-s=${RECORD_SESSION}`, ...rest];
}

function firstLine(res) {
  return (String((res && (res.stderr || res.stdout)) || '').split(/\r?\n/).find(Boolean) || '').slice(0, 200);
}

// 見える形でブラウザを開けなかったときの次の一手。win32 の dashboard は WSL 側の
// playwright-cli を呼ぶので、WSL に表示先が無い環境では必ずここで落ちる。原因は出力に
// 出ているのに「開けませんでした」だけでは動きようがないので、原因ごとに手当てを添える。
function browserOpenHint(detail) {
  const body = String(detail || '');
  if (/XServer|X server|\$?DISPLAY|xvfb|Wayland/i.test(body)) {
    return '（WSL 側に画面がありません。Windows 11 の WSLg を有効にするか、Windows 側で取った'
      + '記録を「別の端末で取った記録」から貼り付けてください）';
  }
  if (/ENOENT|not found|見つかりません/i.test(body)) {
    return '（playwright-cli をこの端末から呼べません。「道具を確認」で確かめてください）';
  }
  if (/install|Executable doesn't exist|browser.*download/i.test(body)) {
    return '（ブラウザの実体が入っていません。`playwright-cli install-browser` を実行してください）';
  }
  return '';
}

// 記録を始める: ブラウザを見える形で開き（人が操作する）、記録を開始する。
async function recordBrowserStart({ cwd = '', url = '', capture, timeoutMs = 60000 } = {}) {
  if (typeof capture !== 'function') throw new Error('記録に使う実行関数がありません');
  const target = text(url, 500);
  const opened = await capture(PLAYWRIGHT_CLI, playwrightArgs('open', '--headed', ...(target ? [target] : [])), { cwd, timeoutMs });
  if (!opened || !opened.ok) {
    const detail = (opened && opened.error) || firstLine(opened) || PLAYWRIGHT_CLI;
    throw new Error(`ブラウザを開けませんでした: ${detail}${browserOpenHint(`${detail} ${(opened && opened.stderr) || ''}`)}`);
  }
  const started = await capture(PLAYWRIGHT_CLI, playwrightArgs('recording-start'), { cwd, timeoutMs });
  if (!started || !started.ok) throw new Error(`記録を開始できませんでした: ${(started && started.error) || firstLine(started)}`);
  return { ok: true, session: RECORD_SESSION, url: target };
}

// 記録を終える: 記録した操作を受け取り、ブラウザを閉じ、工程列へ変換する。
async function recordBrowserStop({ cwd = '', url = '', capture, timeoutMs = 60000 } = {}) {
  if (typeof capture !== 'function') throw new Error('記録に使う実行関数がありません');
  const stopped = await capture(PLAYWRIGHT_CLI, playwrightArgs('recording-stop'), { cwd, timeoutMs });
  // 閉じ損ねても記録は返す（ブラウザが残るだけで、工程は失われない）。
  await capture(PLAYWRIGHT_CLI, playwrightArgs('close'), { cwd, timeoutMs });
  if (!stopped || !stopped.ok) throw new Error(`記録を終了できませんでした: ${(stopped && stopped.error) || firstLine(stopped)}`);
  const raw = String(stopped.stdout || '');
  if (/No actions were recorded/i.test(raw)) throw new Error('操作が記録されていません（ブラウザで操作してから終了してください）');
  return stepsFromRecording({ source: 'browser', text: raw, url });
}

// --- Windows アプリの記録の開始・終了（winauto record の呼び出し） -----------------------

const WINAUTO_CLI = 'winauto';

// 一時ファイルの置き場。**WSL 側の POSIX パス**で持つ——win32 の dashboard は
// `wsl.exe` 越しに winauto を呼び、ラッパーが `--output` / `--stop-file` を Windows パスへ
// 変換して Windows Python に渡す。読み戻しも同じ `wsl.exe` 越しなので、両側が同じ実体を見る。
const RECORD_DIR = '/tmp/agent-dashboard';
const RECORD_SETTLE_TRIES = 8;
const RECORD_SETTLE_WAIT_MS = 400;

// 記録中の一時ファイルの綴りは **main が決めて覚える**。画面から受け取ったパスで
// ファイルを読み書きすると、画面が指した任意の場所を触れることになる（画面は信頼しない）。
let activeWindowsRecording = null;

function windowsRecordingState() {
  return activeWindowsRecording;
}

function resetWindowsRecording() {
  activeWindowsRecording = null;
}

function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

// 書き終わりを待つ。record は 1 行ずつ flush するので途中を読んでも壊れないが、止めた直後は
// まだ最後の数行が出ていないことがある。2 回続けて同じ中身になったら書き終わりとみなす。
async function readSettled(capture, file, cwd, wait) {
  let previous = '';
  for (let i = 0; i < RECORD_SETTLE_TRIES; i += 1) {
    await wait(RECORD_SETTLE_WAIT_MS);
    const res = await capture('cat', [file], { cwd, timeoutMs: 20000 });
    const body = res && res.ok ? String(res.stdout || '') : '';
    if (body && body === previous) return body;
    previous = body;
  }
  return previous;
}

// 記録を始める: `winauto record` を別ウィンドウ（tmux）で走らせる。人はそのウィンドウで
// 進行を見られ、Ctrl+C でも止められる（画面の「終了」は停止ファイルで止める）。
async function recordWindowsStart({ cwd = '', app = '', capture, openWindow, id = '' } = {}) {
  if (typeof capture !== 'function' || typeof openWindow !== 'function') {
    throw new Error('記録に使う実行関数がありません');
  }
  const name = text(app, 120);
  if (!name) throw new Error('記録するアプリ（ウィンドウ名・プロセス名・PID）を入力してください');
  if (activeWindowsRecording) {
    throw new Error('すでに記録中です。先に「記録を終了して工程に起こす」を押してください');
  }
  const key = String(id || Date.now().toString(36));
  const out = `${RECORD_DIR}/record-${key}.jsonl`;
  const stop = `${RECORD_DIR}/record-${key}.stop`;
  // 枠は**外部コマンドを呼ぶ前に**取る。await を跨いでから代入すると、二重に押された
  // 「記録を開始」が両方とも上の検査を通り抜け、2 本目が 1 本目の一時ファイルを忘れさせる。
  const reserved = { app: name, out, stop, cwd };
  activeWindowsRecording = reserved;
  try {
    const made = await capture('mkdir', ['-p', RECORD_DIR], { cwd, timeoutMs: 20000 });
    if (!made || !made.ok) {
      throw new Error(`記録の置き場を作れませんでした: ${(made && made.error) || firstLine(made) || RECORD_DIR}`);
    }
    const res = openWindow({
      command: WINAUTO_CLI,
      args: ['record', '--app', name, '--output', out, '--stop-file', stop],
      cwd,
      sessionKey: 'winauto-record',
      title: '操作の記録',
      message: '別ウィンドウで記録を開始しました。アプリを操作してください',
    });
    if (!res || !res.ok) {
      throw new Error(`記録を開始できませんでした: ${(res && res.error) || 'ウィンドウを開けませんでした'}`);
    }
  } catch (err) {
    // 自分が取った枠のときだけ返す（後から始まった記録の枠を消さない）。
    if (activeWindowsRecording === reserved) activeWindowsRecording = null;
    throw err;
  }
  return { ok: true, source: 'windows', app: name, output: out };
}

// 記録を終える: 停止ファイルを置いて `winauto record` を止め、書けた JSONL を工程列へ。
async function recordWindowsStop({ capture, wait = sleep } = {}) {
  if (typeof capture !== 'function') throw new Error('記録に使う実行関数がありません');
  const rec = activeWindowsRecording;
  if (!rec) throw new Error('記録が始まっていません（「記録を開始」から始めてください）');
  // 停止ファイルを置いた時点で記録は終わり。この先で失敗しても「記録中」へは戻さない
  // （戻すと、止まっている recorder を相手に終了を押し続けることになる）。
  activeWindowsRecording = null;
  await capture('touch', [rec.stop], { cwd: rec.cwd, timeoutMs: 20000 });
  const raw = await readSettled(capture, rec.out, rec.cwd, wait);
  await capture('rm', ['-f', rec.out, rec.stop], { cwd: rec.cwd, timeoutMs: 20000 });
  if (!raw.trim()) {
    throw new Error('記録が空です。記録のウィンドウにエラーが出ていないか確かめてください'
      + `（書き出し先: ${rec.out}）`);
  }
  return stepsFromRecording({ source: 'windows', text: raw, app: rec.app });
}

module.exports = {
  OPS,
  RECORD_DIR,
  WINAUTO_EVENT_KINDS,
  RECORD_SESSION,
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
