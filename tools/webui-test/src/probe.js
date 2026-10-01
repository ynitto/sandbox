'use strict';
// generate --explore --probe-before-act でエージェントに渡す、playwright-cli の見張り役（webui-test browse）。
//
// 状態を変える操作（click / dblclick / fill / select / check / uncheck / 決定キーの press）は、
// 直前に probe で「1 つだけ・見えている・押せる」と確かめた対象にしか行わない。確かめは依頼文だけに
// 任せず、ここで次を機械的に断る:
//   - probe なしの操作（ref や直のセレクタで click しようとした）
//   - 古い probe（そのあとに操作・画面遷移をした / 要素が作り直された / 位置や属性が変わった）
//   - 1 つに決まらない・見えない・押せない対象への probe
// 読むだけのコマンド（snapshot / find / screenshot など）はそのまま playwright-cli に渡す。
//
// 記録（explore-evidence.jsonl）に残すのは、確かめた対象・行った操作・エージェントがはっきり残した観察だけ。
// DOM 全体・依頼文・通信の中身・入力した値は残さない（URL もクエリとハッシュを落とす）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

// 確かめてからでないと行わない操作。press は決定キー（送信・押下になりうるもの）だけ
const PROBE_REQUIRED = new Set(['click', 'dblclick', 'fill', 'select', 'check', 'uncheck', 'press']);
const SUBMIT_KEYS = new Set(['enter', 'numpadenter', 'space', ' ']);
// 読むだけ。probe を古くしない
const READ_ONLY = new Set(['snapshot', 'find', 'screenshot', 'console', 'list', 'tab-list', 'requests', 'request', 'request-headers',
  'request-body', 'response-headers', 'response-body', 'generate-locator', 'cookie-list', 'cookie-get', 'localstorage-list',
  'localstorage-get', 'sessionstorage-list', 'sessionstorage-get', 'route-list', 'highlight', 'webmcp-list']);
// 画面遷移。対象の確かめは要らないが、それまでの probe は古くなる
const NAVIGATION = new Set(['goto', 'go-back', 'go-forward', 'reload', 'tab-new', 'tab-select', 'tab-close']);
// ブラウザの開け閉めは webui-test が持つ
const LIFECYCLE = new Set(['open', 'close', 'attach', 'detach', 'close-all', 'kill-all', 'delete-data']);

const TARGET_KEYS = { role: 'role', name: 'name', label: 'label', text: 'text', placeholder: 'placeholder', 'test-id': 'testId', alt: 'alt', title: 'title', css: 'css' };

function classify(cmd, args = []) {
  if (cmd === 'probe' || cmd === 'observe') return cmd;
  if (LIFECYCLE.has(cmd)) return 'lifecycle';
  if (READ_ONLY.has(cmd)) return 'read';
  if (NAVIGATION.has(cmd)) return 'navigation';
  if (cmd === 'press') {
    const plain = takeOption(takeOption(args, 'probe').rest, 'fingerprint').rest;
    const key = String(plain.find((a) => !a.startsWith('--')) || '').toLowerCase();
    return SUBMIT_KEYS.has(key) ? 'action' : 'other';
  }
  if (PROBE_REQUIRED.has(cmd)) return 'action';
  return 'other';
}

// --role button --name 保存 などをテストケースの書式と同じ対象（{ role, name, exact, nth }）にする
function parseTarget(args) {
  const t = {};
  const rest = [];
  for (let i = 0; i < args.length; i += 1) {
    const m = /^--([\w-]+)(?:=(.*))?$/.exec(args[i]);
    if (!m) { rest.push(args[i]); continue; }
    const [, key, inline] = m;
    if (key === 'exact') { t.exact = inline === undefined ? true : inline !== 'false'; continue; }
    const value = inline !== undefined ? inline : args[++i];
    if (value === undefined) throw new Error(`--${key} に値がありません`);
    if (key === 'nth') t.nth = Number(value);
    else if (TARGET_KEYS[key]) t[TARGET_KEYS[key]] = value;
    else rest.push(`--${key}`, value);
  }
  return { target: Object.keys(t).length ? t : null, rest };
}

function validTarget(t) {
  if (!t) return '対象を --role（と --name）/ --label / --text / --placeholder / --test-id / --alt / --title / --css のどれかで指定してください';
  const kinds = ['css', 'role', 'label', 'text', 'placeholder', 'testId', 'alt', 'title'].filter((k) => t[k] !== undefined);
  if (!kinds.length) return '--name は --role と一緒に使います';
  if (kinds.length > 1) return `対象の指定は 1 種類にしてください（${kinds.join(' と ')}）`;
  if (t.name !== undefined && !t.role) return '--name は --role と一緒に使います';
  if (t.nth !== undefined && !Number.isInteger(t.nth)) return '--nth は整数です';
  return null;
}

// runner.js の toLocator と同じ解釈を、playwright-cli の run-code で動く式にする
function locatorExpr(t) {
  const j = JSON.stringify;
  const opt = t.exact !== undefined ? { exact: t.exact } : {};
  let e;
  if (t.css) e = `page.locator(${j(t.css)})`;
  else if (t.role) e = `page.getByRole(${j(t.role)}, ${j({ ...opt, ...(t.name !== undefined ? { name: String(t.name) } : {}) })})`;
  else if (t.label) e = `page.getByLabel(${j(String(t.label))}, ${j(opt)})`;
  else if (t.text) e = `page.getByText(${j(String(t.text))}, ${j(opt)})`;
  else if (t.placeholder) e = `page.getByPlaceholder(${j(String(t.placeholder))}, ${j(opt)})`;
  else if (t.testId) e = `page.getByTestId(${j(String(t.testId))})`;
  else if (t.alt) e = `page.getByAltText(${j(String(t.alt))}, ${j(opt)})`;
  else if (t.title) e = `page.getByTitle(${j(String(t.title))}, ${j(opt)})`;
  if (t.nth !== undefined) e += `.nth(${Number(t.nth)})`;
  return e;
}

// 記録に残す URL。クエリ・ハッシュにはトークンが入りうるので落とす
function safeUrl(u) {
  try { const x = new URL(u); return x.origin === 'null' ? x.protocol : x.origin + x.pathname; } catch (_) { return null; }
}

// ページの中で要素の特徴を集める（値は読まない）。指紋はこれのハッシュで、中身は記録に残さない
const DESCRIBE = `(el) => {
  const r = el.getBoundingClientRect();
  const a = (n) => el.getAttribute(n);
  return {
    tag: el.tagName.toLowerCase(), type: a('type'), role: a('role'), id: el.id || null, name: a('name'),
    aria: a('aria-label'), labelledby: a('aria-labelledby'), expanded: a('aria-expanded'),
    text: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ? null : (el.innerText || el.textContent || '').trim().slice(0, 200),
    disabled: !!el.disabled || a('aria-disabled') === 'true',
    bbox: { x: Math.round(r.x + scrollX), y: Math.round(r.y + scrollY), width: Math.round(r.width), height: Math.round(r.height) },
  };
}`;

function fingerprint(desc) {
  return 'sha256:' + crypto.createHash('sha256').update(JSON.stringify(desc)).digest('hex');
}

function probeCode(t, { id, shot }) {
  return `async page => {
  const loc = ${locatorExpr(t)};
  const matches = await loc.count();
  const out = { matches, visibleMatches: 0, url: page.url() };
  for (let i = 0; i < Math.min(matches, 20); i += 1) if (await loc.nth(i).isVisible().catch(() => false)) out.visibleMatches += 1;
  if (matches !== 1) return out;
  out.visible = await loc.isVisible();
  out.enabled = await loc.isEnabled().catch(() => false);
  out.desc = await loc.evaluate(${DESCRIBE});
  if (out.visible && out.enabled) {
    await loc.evaluate((el, id) => {
      if (!window.__webTestProbes) Object.defineProperty(window, '__webTestProbes', { value: {}, enumerable: false });
      window.__webTestProbes[id] = new WeakRef(el);
    }, ${JSON.stringify(id)});
  }
  ${shot ? `if (out.visible) out.shot = await loc.screenshot({ path: ${JSON.stringify(shot)}, timeout: 3000 }).then(() => true, () => false);` : ''}
  return out;
}`;
}

const SAME_ELEMENT = `(el, id) => { const m = window.__webTestProbes; const r = m && m[id]; return !!(r && r.deref() === el); }`;

// 1 回目: 同じ要素のままか・まだ押せるかを確かめ、指紋を取り直す
function checkCode(t, id) {
  return `async page => {
  const loc = ${locatorExpr(t)};
  const matches = await loc.count();
  if (matches !== 1) return { rejected: matches ? 'ambiguous' : 'missing', matches };
  if (!(await loc.evaluate(${SAME_ELEMENT}, ${JSON.stringify(id)}))) return { rejected: 'stale', detail: '要素が作り直されたか、別の画面に移りました' };
  const visible = await loc.isVisible();
  const enabled = await loc.isEnabled().catch(() => false);
  if (!visible || !enabled) return { rejected: 'not-ready', visible, enabled };
  return { desc: await loc.evaluate(${DESCRIBE}) };
}`;
}

// 2 回目: もう一度同じ要素かを確かめてから操作する（値はここにしか書かない）
function actCode(t, id, action, value, timeout) {
  const j = JSON.stringify;
  const op = {
    click: `await loc.click({ timeout: ${timeout} })`,
    dblclick: `await loc.dblclick({ timeout: ${timeout} })`,
    fill: `await loc.fill(${j(String(value ?? ''))}, { timeout: ${timeout} })`,
    select: `await loc.selectOption(${j(value)}, { timeout: ${timeout} })`,
    check: `await loc.check({ timeout: ${timeout} })`,
    uncheck: `await loc.uncheck({ timeout: ${timeout} })`,
    press: `await loc.press(${j(String(value))}, { timeout: ${timeout} })`,
  }[action];
  return `async page => {
  const loc = ${locatorExpr(t)};
  if (!(await loc.evaluate(${SAME_ELEMENT}, ${j(id)}).catch(() => false))) return { rejected: 'stale', detail: '確かめた直後に要素が変わりました' };
  ${op};
  await page.waitForLoadState('domcontentloaded', { timeout: ${timeout} }).catch(() => {});
  return { url: page.url() };
}`;
}

// ---- 状態（probe の一覧と世代）。依頼の作業ディレクトリに置き、generate の終わりに消える ----

function emptyStats() {
  return {
    probes: 0, ready: 0, ambiguous: 0, missing: 0, hidden: 0, disabled: 0,
    actions: 0, failed: 0, navigations: 0, unguarded: 0, observations: 0,
    rejected: { missingProbe: 0, unknownProbe: 0, stale: 0, ambiguous: 0, missing: 0, notReady: 0, changed: 0, fingerprintMismatch: 0, lifecycle: 0 },
  };
}

function initState(file) {
  const s = { seq: 0, generation: 0, probes: {}, stats: emptyStats() };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(s));
  return s;
}

function loadState(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return initState(file); }
}

function saveState(file, s) {
  fs.writeFileSync(file, JSON.stringify(s));
}

// probe を操作に使えるか（ページに触らずに決められる分）。理由の名前は stats.rejected のキー
function checkProbe(state, id, { fingerprint: given } = {}) {
  if (!id) return { reason: 'missingProbe', message: '状態を変える操作の前に probe で対象を確かめ、--probe <ID> を付けてください' };
  const p = state.probes[id];
  if (!p) return { reason: 'unknownProbe', message: `probe「${id}」はありません` };
  if (!p.ready) return { reason: p.matches > 1 ? 'ambiguous' : p.matches === 0 ? 'missing' : 'notReady', message: `probe「${id}」の対象は操作できる状態ではありません（${p.why}）` };
  if (p.generation !== state.generation) return { reason: 'stale', message: `probe「${id}」は古くなっています（そのあとに操作か画面遷移がありました）。もう一度 probe してください` };
  if (given && given !== p.fingerprint) return { reason: 'fingerprintMismatch', message: `--fingerprint が probe「${id}」の指紋と違います` };
  return null;
}

function notReadyReason(r) {
  if (r.matches === 0) return { key: 'missing', why: '一致する要素がありません' };
  if (r.matches > 1) return { key: 'ambiguous', why: `${r.matches} 個に一致します（見えているのは ${r.visibleMatches} 個）。役割・名前・exact などで 1 つに絞ってください` };
  if (!r.visible) return { key: 'hidden', why: '要素は隠れています' };
  if (!r.enabled) return { key: 'disabled', why: '要素は押せない（無効）状態です' };
  return null;
}

// ---- 記録 ----

function appendEvidence(dir, record) {
  fs.mkdirSync(dir, { recursive: true });
  fs.appendFileSync(path.join(dir, 'explore-evidence.jsonl'), JSON.stringify({ at: new Date().toISOString(), ...record }) + '\n');
}

// 操作の記録。入力した値は残さない（パスワードかどうかをここで見分けきれないので、すべて伏せる）
function actionRecord({ action, id, target, fp, value, url, status, reason, message }) {
  const rec = { type: 'action', action, probeId: id || null, target: target || null, fingerprint: fp || null, status };
  if (action === 'fill') rec.value = '[伏せた]';
  else if (action === 'select' && value !== undefined) rec.value = value;
  else if (action === 'press' && value !== undefined) rec.key = value;
  if (url) rec.url = url; // 操作のあとの URL
  if (reason) rec.reason = reason;
  if (message) rec.message = message;
  return rec;
}

// ---- playwright-cli の呼び出し ----

function runCli(argv, { capture, cwd }) {
  return new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd: cwd || process.cwd(),
      windowsHide: true,
      stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
      shell: process.platform === 'win32' && argv[0] !== process.execPath && !/\.exe$/i.test(argv[0]),
    });
    let out = '';
    if (capture) {
      child.stdout.on('data', (d) => { out += d; });
      child.stderr.on('data', (d) => { out += d; });
    }
    child.on('error', (e) => resolve({ code: -1, out: e.message }));
    child.on('close', (code) => resolve({ code, out }));
  });
}

async function runCode(base, code) {
  const r = await runCli([...base, '--raw', 'run-code', code], { capture: true });
  const text = r.out.trim();
  if (r.code !== 0) throw new Error(`playwright-cli でページを調べられません: ${text.split('\n').filter(Boolean).slice(-2).join(' ')}`);
  try { return JSON.parse(text); } catch (_) { throw new Error(`playwright-cli の応答を読めません: ${text.slice(0, 300)}`); }
}

// ---- webui-test browse ----

const BROWSE_USAGE = `webui-test browse --session <名前> --state <file> --evidence <dir> <コマンド> [引数]
  generate --explore --probe-before-act がエージェントに渡す playwright-cli の代わり。
  probe --role <役割> --name <名前>      対象が 1 つだけ・見えている・押せるかを確かめる（--label / --text / --placeholder /
                                         --test-id / --alt / --title / --css / --exact / --nth も使える）
  click|dblclick|check|uncheck --probe <ID>         確かめた対象を操作する
  fill --probe <ID> "<文字>" / select --probe <ID> <値> / press Enter --probe <ID>
  observe "<気づいたこと>" [対象]           テストに書く観察を記録に残す
  そのほか（snapshot / find / screenshot / goto など）は playwright-cli にそのまま渡す`;

function parseBrowseArgs(argv) {
  const o = { session: process.env.WEBUI_TEST_SESSION, state: process.env.WEBUI_TEST_PROBE_STATE, evidence: process.env.WEBUI_TEST_EVIDENCE_DIR, timeout: 5000 };
  let i = 0;
  for (; i < argv.length; i += 1) {
    const m = /^--(session|state|evidence|timeout)(?:=(.*))?$/.exec(argv[i]);
    if (!m) break;
    o[m[1]] = m[2] !== undefined ? m[2] : argv[++i];
  }
  o.timeout = Number(o.timeout) || 5000;
  o.cmd = argv[i];
  o.args = argv.slice(i + 1);
  return o;
}

function takeOption(args, name) {
  const out = [];
  let value;
  for (let i = 0; i < args.length; i += 1) {
    const m = new RegExp(`^--${name}(?:=(.*))?$`).exec(args[i]);
    if (m) value = m[1] !== undefined ? m[1] : args[++i];
    else out.push(args[i]);
  }
  return { value, rest: out };
}

async function browseMain(argv, io = { out: process.stdout, err: process.stderr }, deps = {}) {
  const o = parseBrowseArgs(argv);
  const say = (obj) => io.out.write((typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2)) + '\n');
  if (!o.cmd || o.cmd === '--help' || o.cmd === 'help') { say(BROWSE_USAGE); return o.cmd ? 0 : 2; }
  if (!o.session || !o.state || !o.evidence) { io.err.write('--session / --state / --evidence が要ります（generate --probe-before-act が渡すコマンドをそのまま使ってください）\n'); return 2; }
  const bin = deps.bin || require('./generate').findPlaywrightCli();
  const base = [...bin, `-s=${o.session}`];
  const state = loadState(o.state);
  const kind = classify(o.cmd, o.args);
  const done = (code) => { saveState(o.state, state); return code; };

  if (kind === 'read') return (await runCli([...base, o.cmd, ...o.args], {})).code;
  if (kind === 'lifecycle') {
    state.stats.rejected.lifecycle += 1;
    io.err.write(`「${o.cmd}」は使えません。ブラウザは webui-test が開け閉めします\n`);
    return done(1);
  }
  if (kind === 'navigation' || kind === 'other') {
    // 画面遷移や、見張れない操作（eval・type・drag など）のあとは、それまでの probe を古いものとして扱う
    const r = await runCli([...base, o.cmd, ...o.args], {});
    state.generation += 1;
    if (kind === 'navigation') state.stats.navigations += 1;
    else state.stats.unguarded += 1;
    return done(r.code);
  }

  if (kind === 'probe') {
    const { target, rest } = parseTarget(o.args);
    const bad = validTarget(target) || (rest.length ? `知らない引数: ${rest.join(' ')}` : null);
    if (bad) { io.err.write(bad + '\n'); return 2; }
    state.seq += 1;
    const id = `probe-${String(state.seq).padStart(4, '0')}`;
    const shotRel = `evidence/${id}.png`;
    const shot = path.resolve(o.evidence, shotRel);
    fs.mkdirSync(path.dirname(shot), { recursive: true });
    let r;
    try { r = await runCode(base, probeCode(target, { id, shot })); } catch (e) { io.err.write(e.message + '\n'); return done(1); }
    const nr = notReadyReason(r);
    const result = {
      type: 'probe', probeId: id, target, url: safeUrl(r.url), matches: r.matches,
      ...(r.matches !== 1 ? { visibleMatches: r.visibleMatches } : { visible: r.visible, enabled: r.enabled, bbox: r.desc.bbox }),
      ready: !nr, ...(nr ? { reason: nr.why } : {}),
      ...(r.shot ? { screenshot: shotRel } : {}),
      ...(r.desc ? { fingerprint: fingerprint(r.desc) } : {}),
    };
    state.stats.probes += 1;
    if (nr) state.stats[nr.key] += 1; else state.stats.ready += 1;
    state.probes[id] = { target, generation: state.generation, ready: !nr, matches: r.matches, why: nr ? nr.why : null, fingerprint: result.fingerprint || null };
    appendEvidence(o.evidence, result);
    say(result);
    return done(nr ? 1 : 0);
  }

  if (kind === 'observe') {
    const { target, rest } = parseTarget(o.args);
    const note = rest.filter((a) => !a.startsWith('--')).join(' ').trim();
    if (!note) { io.err.write('observe "<気づいたこと>" の形で書いてください\n'); return 2; }
    const rec = { type: 'observation', note: note.slice(0, 500) };
    try {
      const page = await runCode(base, target
        ? `async page => { const l = ${locatorExpr(target)}; const n = await l.count(); return { url: page.url(), matches: n, visible: n === 1 ? await l.isVisible() : null }; }`
        : 'async page => ({ url: page.url() })');
      rec.url = safeUrl(page.url);
      if (target) Object.assign(rec, { target, matches: page.matches, ...(page.visible !== null ? { visible: page.visible } : {}) });
    } catch (e) { io.err.write(e.message + '\n'); return done(1); }
    state.stats.observations += 1;
    appendEvidence(o.evidence, rec);
    say(rec);
    return done(0);
  }

  // 状態を変える操作
  const { value: id, rest: a1 } = takeOption(o.args, 'probe');
  const { value: given, rest: a2 } = takeOption(a1, 'fingerprint');
  const action = o.cmd;
  const value = action === 'fill' ? a2.join(' ') : action === 'select' ? (a2.length > 1 ? a2 : a2[0]) : action === 'press' ? a2[0] : undefined;
  const reject = (reason, message, extra = {}) => {
    state.stats.rejected[reason] += 1;
    const p = id && state.probes[id];
    appendEvidence(o.evidence, actionRecord({ action, id, target: p && p.target, value, status: 'rejected', reason, message }));
    say({ type: 'action', action, probeId: id || null, status: 'rejected', reason, message, ...extra });
    return done(1);
  };
  if (!id && a2.some((x) => /^e\d+$/.test(x) || x.startsWith('aria-ref='))) {
    return reject('missingProbe', 'snapshot の ref では操作できません。probe で対象を確かめ、--probe <ID> を付けてください');
  }
  const pre = checkProbe(state, id, { fingerprint: given });
  if (pre) return reject(pre.reason, pre.message);
  if ((action === 'fill' || action === 'select' || action === 'press') && (value === undefined || value === '')) {
    io.err.write(`${action} には値が要ります\n`);
    return 2;
  }
  const p = state.probes[id];
  let check;
  try { check = await runCode(base, checkCode(p.target, id)); } catch (e) { io.err.write(e.message + '\n'); return done(1); }
  if (check.rejected) {
    const reason = { ambiguous: 'ambiguous', missing: 'missing', stale: 'stale', 'not-ready': 'notReady' }[check.rejected];
    return reject(reason, check.detail || `対象が操作できる状態ではありません（${JSON.stringify(check)}）。もう一度 probe してください`);
  }
  const fp = fingerprint(check.desc);
  if (fp !== p.fingerprint) return reject('changed', '確かめたあとに対象の位置か属性が変わりました。もう一度 probe してください', { fingerprint: fp });
  // ここから先は操作する。成功しても失敗しても、それまでの probe は古くなる
  state.generation += 1;
  let r;
  try {
    r = await runCode(base, actCode(p.target, id, action, value, o.timeout));
  } catch (e) {
    state.stats.failed += 1;
    appendEvidence(o.evidence, actionRecord({ action, id, target: p.target, fp, value, status: 'failed', message: e.message.slice(0, 300) }));
    io.err.write(e.message + '\n');
    return done(1);
  }
  if (r.rejected) return reject('stale', r.detail);
  state.stats.actions += 1;
  const rec = actionRecord({ action, id, target: p.target, fp, value, url: safeUrl(r.url), status: 'done' });
  appendEvidence(o.evidence, rec);
  say(rec);
  return done(0);
}

module.exports = {
  browseMain, classify, parseTarget, validTarget, locatorExpr, safeUrl, fingerprint, checkProbe, notReadyReason,
  actionRecord, initState, loadState, emptyStats, BROWSE_USAGE,
};
