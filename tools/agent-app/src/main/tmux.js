'use strict';

// 会話 1 つ = tmux セッション 1 つ。エージェント CLI を定義の interactive 節で対話起動し、
//   - 依頼は send-keys / paste-buffer でペインへ流し込む
//   - 画面は capture-pane を回して写す（端末ミラー。node-pty も attach も使わない）
//   - ターンの終わりは ready_pattern / busy_pattern / idle_quiet_sec（定義ファイル）で見る
//   - 応答本文は「送信前後のスクロールバックの差分」から入力欄などの飾りを除いて拾う
//
// tmux サーバは自前のソケット（-L agent-app）で持つ。利用者の tmux（既定サーバ）の
// オプションや一覧に干渉しないためで、人が覗くときは `tmux -L agent-app attach -t <名前>`。
//
// ホスト（Linux / macOS のローカル、Windows なら WSL）との会話は host.js の常駐シェル経由。

const { stripAnsi, ereToRegExp } = require('./text');
const host = require('./host');

const SOCKET = 'agent-app';
const TMUX = `tmux -L ${SOCKET}`;
const HISTORY_LIMIT = 50000;
const DEFAULT_COLS = 120;
const DEFAULT_ROWS = 36;

// 定義に ready_pattern が無いときの既定（schema の説明どおり: 素のプロンプト記号・枠付き入力欄・kiro の入力プレースホルダ）
const DEFAULT_READY = '^[[:space:]]*[>?❯›][[:space:]]*$|│[[:space:]]*[>❯›]|ask a question|describe a task';
// 端末側で人の判断を待っている（ツール実行の許可、y/n）らしい画面
const ATTENTION = /do you want|allow this|permission|\[y\/n\]|\(y\/n\)|yes, and don't ask|❯\s*1\.\s*yes|approve|continue\?/i;

function sessionName(id) {
  return `agent-app-${String(id || '').replace(/[^0-9a-zA-Z]/g, '').slice(0, 12)}`;
}

// ---- 画面の判定 ---------------------------------------------------------------

// 入力欄・枠線・フッターなど、応答本文ではない行
const CHROME = [
  /^\s*$/,
  /^[\s─━═╌┄┈╭╮╰╯│┃┌┐└┘├┤┬┴┼▔▁▏▕▖▗▘▙▚▛▜▝▞▟■□●○◆◇═╔╗╚╝╠╣╦╩╬]+$/,
  /^\s*[>?❯›$!]+\s*$/,
  /^\s*[│┃]\s*[>❯›]/,
  /\? for shortcuts|esc to interrupt|ask a question|describe a task|ctrl\+c to|shift\+tab|\bcontext left\b|\d+% context|tokens used|bypass permissions|accept edits|plan mode/i,
  /^\s*(?:\/|~|\.\.\/)?[\w.\-/~]*\s*(?:\(|\[)?(?:main|master)?(?:\)|\])?\s*$/,   // ステータス行に出る cwd / ブランチ
];
function isChrome(line) {
  const s = String(line);
  return CHROME.some((re) => re.test(s));
}

function tailLines(text, n) {
  const lines = String(text).split('\n').filter((l) => l.trim());
  return lines.slice(-Math.max(1, n)).join('\n');
}

// 画面（ANSI 剥がし済み）から待機 / 処理中 / 確認待ちを判定する。
//   state: 'ready' | 'busy' | 'unknown'
function classify(screen, patterns) {
  const text = String(screen);
  if (patterns.busy && patterns.busy.test(text)) return 'busy';
  if (patterns.ready && patterns.ready.test(tailLines(text, patterns.readyTailLines))) return 'ready';
  return 'unknown';
}

function compilePatterns(inter) {
  const i = inter || {};
  return {
    ready: ereToRegExp(i.readyPattern || DEFAULT_READY) || ereToRegExp(DEFAULT_READY),
    busy: i.busyPattern ? ereToRegExp(i.busyPattern) : null,
    failure: i.failurePattern ? ereToRegExp(i.failurePattern) : null,
    readyTailLines: Number(i.readyTailLines) > 0 ? Number(i.readyTailLines) : 3,
    readyTimeoutSec: Number(i.readyTimeoutSec) > 0 ? Number(i.readyTimeoutSec) : 60,
    idleQuietSec: Number(i.idleQuietSec) > 0 ? Number(i.idleQuietSec) : 0,
  };
}

// 送信前後のスクロールバック（-J で折り返しを戻した素のテキスト）から応答本文を取り出す。
// 送信前の末尾にあった入力欄・フッターは上書きされて消えるので、共通の先頭部分だけを
// 「既に見た」とみなし、その先を差分とする。差分から飾りと依頼の echo を落とす。
function extractReply(before, after, prompt) {
  const b = String(before).split('\n');
  const a = String(after).split('\n');
  while (b.length && isChrome(b[b.length - 1])) b.pop();
  let i = 0;
  while (i < b.length && i < a.length && b[i] === a[i]) i += 1;
  let delta = a.slice(i);
  const promptLines = new Set(String(prompt || '').split('\n').map((l) => l.trim()).filter(Boolean));
  const echo = (line) => {
    const t = line.replace(/^\s*[>❯›│┃]+\s*/, '').trim();
    return t && promptLines.has(t);
  };
  // 先頭: 依頼の echo と飾り。末尾: 入力欄・フッター。
  while (delta.length && (isChrome(delta[0]) || echo(delta[0]))) delta.shift();
  while (delta.length && isChrome(delta[delta.length - 1])) delta.pop();
  delta = delta.filter((line) => !echo(line));
  return delta.join('\n').replace(/[ \t]+$/gm, '').trim();
}

// ---- xterm のキー入力 → tmux send-keys -------------------------------------

const KEY_NAMES = {
  '\r': 'Enter', '\n': 'Enter', '\t': 'Tab', '\x7f': 'BSpace', '\b': 'BSpace', '\x1b': 'Escape', '\x03': 'C-c', '\x04': 'C-d', '\x1a': 'C-z',
  '\x1b[A': 'Up', '\x1b[B': 'Down', '\x1b[C': 'Right', '\x1b[D': 'Left', '\x1bOA': 'Up', '\x1bOB': 'Down', '\x1bOC': 'Right', '\x1bOD': 'Left',
  '\x1b[H': 'Home', '\x1b[F': 'End', '\x1bOH': 'Home', '\x1bOF': 'End', '\x1b[1~': 'Home', '\x1b[4~': 'End',
  '\x1b[2~': 'IC', '\x1b[3~': 'DC', '\x1b[5~': 'PPage', '\x1b[6~': 'NPage',
  '\x1b[Z': 'BTab', '\x1bOP': 'F1', '\x1bOQ': 'F2', '\x1bOR': 'F3', '\x1bOS': 'F4',
};

// xterm の onData が寄越す文字列を tmux の send-keys 引数列に分ける。
// 名前のあるキーは名前で（-l を付けない）、それ以外の文字は -l で素のまま送る。
function keysToArgs(data) {
  const out = [];
  let literal = '';
  const flush = () => { if (literal) { out.push(['-l', '--', literal]); literal = ''; } };
  let i = 0;
  const s = String(data);
  while (i < s.length) {
    let hit = '';
    for (const len of [4, 3, 2, 1]) {
      const cand = s.slice(i, i + len);
      if (cand.length === len && KEY_NAMES[cand]) { hit = cand; break; }
    }
    if (hit) { flush(); out.push(['--', KEY_NAMES[hit]]); i += hit.length; continue; }
    const ch = s[i];
    if (ch < ' ' && ch !== '\x1b') { flush(); out.push(['--', `C-${String.fromCharCode(ch.charCodeAt(0) + 96)}`]); i += 1; continue; }
    literal += ch;
    i += 1;
  }
  flush();
  return out;
}

// ---- tmux コマンド（ホストのシェルへ渡すスクリプト） ----------------------------

const sq = host.sq;

function cmdHas(name) { return `${TMUX} has-session -t ${sq(`=${name}`)} 2>/dev/null`; }

function cmdNew({ name, cwd, argv, cols, rows }) {
  // ペインの中身は `bash -lc 'exec <argv>'`。ログインシェルで PATH を揃え、CLI が終わっても
  // remain-on-exit で画面（エラーの理由）を残す。
  const inner = `exec ${host.quoteArgv(argv)}`;
  return [
    `${TMUX} new-session -d -s ${sq(name)} -c ${sq(cwd)} -x ${Number(cols) || DEFAULT_COLS} -y ${Number(rows) || DEFAULT_ROWS} bash -lc ${sq(inner)}`,
    `${TMUX} set-option -g history-limit ${HISTORY_LIMIT} >/dev/null`,
    `${TMUX} set-option -g status off >/dev/null`,
    `${TMUX} set-option -t ${sq(name)} remain-on-exit on >/dev/null`,
    `${TMUX} set-option -t ${sq(name)} window-size manual >/dev/null`,
    `${TMUX} set-option -t ${sq(name)} mouse off >/dev/null`,
  ].join(' && ');
}

// 画面の状態 1 行（| 区切り。tmux は書式出力の制御文字を \037 のような 8 進表記へ直すので
// 区切りは印字可能な文字にする）+ 画面本体（色付き）。頭と本体の間の \036 は自前の printf で出す。
function cmdScreen(name, { history = false } = {}) {
  const fmt = '#{cursor_x}|#{cursor_y}|#{pane_width}|#{pane_height}|#{pane_dead}|#{pane_dead_status}|#{history_size}|#{pane_in_mode}';
  const cap = history ? `capture-pane -p -J -S - -t ${sq(name)}` : `capture-pane -p -e -t ${sq(name)}`;
  return `${TMUX} display-message -p -t ${sq(name)} ${sq(fmt)} && printf '\\036' && ${TMUX} ${cap}`;
}

function parseScreen(output) {
  const at = output.indexOf('\x1e');
  if (at < 0) return null;
  const head = output.slice(0, at).replace(/\n$/, '').split('|');
  const body = output.slice(at + 1);
  return {
    cursor: { x: Number(head[0]) || 0, y: Number(head[1]) || 0 },
    cols: Number(head[2]) || DEFAULT_COLS,
    rows: Number(head[3]) || DEFAULT_ROWS,
    dead: head[4] === '1',
    deadStatus: head[5] === '' ? null : Number(head[5]),
    historySize: Number(head[6]) || 0,
    inMode: head[7] === '1',
    text: body,
  };
}

function cmdKeys(name, args) {
  return `${TMUX} send-keys -t ${sq(name)} ${host.quoteArgv(args)}`;
}

function cmdKill(name) { return `${TMUX} kill-session -t ${sq(`=${name}`)}`; }

function cmdResize(name, cols, rows) {
  return `${TMUX} resize-window -t ${sq(name)} -x ${Number(cols) || DEFAULT_COLS} -y ${Number(rows) || DEFAULT_ROWS}`;
}

function cmdList() {
  return `${TMUX} list-sessions -F '#{session_name}' 2>/dev/null || true`;
}

// ---- 会話 1 つ分の駆動 ----------------------------------------------------------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Conversation {
  // { id, shell, cwd, argv, patterns, emit(event, payload), cols, rows }
  constructor(opts) {
    this.id = opts.id;
    this.name = sessionName(opts.id);
    this.shell = opts.shell;
    this.cwd = opts.cwd;
    this.argv = opts.argv;
    this.patterns = opts.patterns;
    this.emit = opts.emit || (() => {});
    this.cols = opts.cols || DEFAULT_COLS;
    this.rows = opts.rows || DEFAULT_ROWS;
    this.phase = 'starting';         // starting | ready | busy | attention | dead | gone
    this.detail = '';
    this.launch = opts.launch || null;   // 今動いている CLI の起動条件 { cli, model, readonly }（ipc が入れる）
    this.seen = 0;                       // この CLI の文脈に入っているメッセージ数（ipc が進める）
    this.resumed = true;                 // 起動時に CLI 側の文脈を引き継げたか
    this.turn = null;                // { prompt, startedAt, before, sawBusy, readyCount, stopped, done(message) }
    this.watchers = 0;               // 端末ミラーを見ている画面の数（0 なら間隔を落とす）
    this.lastScreen = null;
    this.lastText = '';
    this.lastChangeAt = Date.now();
    this.startedAt = Date.now();
    this.timer = null;
    this.polling = false;
    this.closed = false;
  }

  async exists() {
    return (await this.shell.run(cmdHas(this.name))).ok;
  }

  // 既にある tmux セッションへつなぐか、無ければ CLI を起動する。reuse=false なら
  // 残っているセッションを消してから起動し直す（別の CLI・別のモデルで続けるとき）。
  async open({ reuse = true } = {}) {
    const alive = await this.exists();
    if (!reuse && alive) await this.shell.run(cmdKill(this.name));
    if (!(reuse && alive)) {
      const r = await this.shell.run(cmdNew({ name: this.name, cwd: this.cwd, argv: this.argv, cols: this.cols, rows: this.rows }), { timeoutMs: 30000 });
      if (!r.ok) throw new Error(`tmux セッションを作れません: ${r.error || r.output}`);
      this.setPhase('starting', '起動中');
    } else {
      this.setPhase('starting', '既存の tmux セッションへ再接続');
    }
    this.startedAt = Date.now();
    this.schedule(0);
    return { name: this.name, reused: reuse && alive };
  }

  setPhase(phase, detail = '') {
    if (this.phase === phase && this.detail === detail) return;
    this.phase = phase;
    this.detail = detail;
    this.emit('term:phase', { id: this.id, phase, detail, name: this.name });
  }

  schedule(ms) {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.poll().catch(() => {}), ms);
  }

  interval() {
    if (this.watchers > 0 || this.turn) return 250;
    return this.phase === 'dead' || this.phase === 'gone' ? 5000 : 1200;
  }

  async capture({ history = false } = {}) {
    const r = await this.shell.run(cmdScreen(this.name, { history }), { timeoutMs: 10000 });
    if (!r.ok) return { ok: false, error: r.error || r.output };
    const screen = parseScreen(r.output);
    if (!screen) return { ok: false, error: 'capture-pane の出力を読めません' };
    return { ok: true, screen };
  }

  async poll() {
    if (this.closed || this.polling) return;
    this.polling = true;
    try {
      const cap = await this.capture();
      if (!cap.ok) {
        if (/can't find|no server|no such/i.test(cap.error)) {
          this.setPhase('gone', 'tmux セッションが無い');
          if (this.turn) this.finishTurn({ error: 'tmux セッションが消えました' });
        }
        return;
      }
      const screen = cap.screen;
      const text = stripAnsi(screen.text);
      const changed = text !== this.lastText;
      if (changed) { this.lastText = text; this.lastChangeAt = Date.now(); }
      this.lastScreen = screen;
      if (this.watchers > 0 && (changed || !this.sentOnce)) {
        this.sentOnce = true;
        this.emit('term:screen', { id: this.id, text: screen.text, cursor: screen.cursor, cols: screen.cols, rows: screen.rows, tail: tailLines(text, 14) });
      }
      if (screen.dead) {
        this.setPhase('dead', `CLI が終了しました（終了コード ${screen.deadStatus == null ? '?' : screen.deadStatus}）`);
        if (this.turn) this.finishTurn({ error: this.detail, text: extractReply(this.turn.before, (await this.historyText()) || text, this.turn.prompt) });
        return;
      }
      let state = classify(text, this.patterns);
      if (state === 'unknown' && this.patterns.idleQuietSec > 0 && Date.now() - this.lastChangeAt >= this.patterns.idleQuietSec * 1000) state = 'ready';
      const attention = state !== 'ready' && ATTENTION.test(text);
      if (this.turn) await this.trackTurn(state, attention, text);
      else if (this.phase === 'starting') {
        if (state === 'ready') this.setPhase('ready', '');
        else if (Date.now() - this.startedAt > this.patterns.readyTimeoutSec * 1000) this.setPhase('ready', '入力受付を確認できないまま待機扱い');
        else if (attention) this.setPhase('attention', '端末で確認を求めています');
      } else if (attention) this.setPhase('attention', '端末で確認を求めています');
      else if (state === 'ready') this.setPhase('ready', '');
      else if (state === 'busy') this.setPhase('busy', '端末側で処理中');
    } finally {
      this.polling = false;
      this.schedule(this.interval());
    }
  }

  async historyText() {
    const cap = await this.capture({ history: true });
    return cap.ok ? stripAnsi(cap.screen.text) : '';
  }

  async trackTurn(state, attention, text) {
    const t = this.turn;
    const elapsed = Date.now() - t.startedAt;
    if (t.stopped && state === 'ready') return this.finishTurn({ stopped: true });
    if (this.patterns.failure && this.patterns.failure.test(text)) return this.finishTurn({ error: '定義の failure_pattern に一致しました' });
    if (state === 'busy' || state === 'unknown') t.sawBusy = true;
    if (attention) { this.setPhase('attention', '端末で確認を求めています'); t.readyCount = 0; return; }
    if (state === 'ready' && (t.sawBusy || elapsed > 2500)) {
      t.readyCount += 1;
      if (t.readyCount >= 2) return this.finishTurn({});
    } else {
      t.readyCount = 0;
      this.setPhase('busy', '応答中');
    }
    return undefined;
  }

  async finishTurn({ error = '', stopped = false, text = null }) {
    const t = this.turn;
    if (!t) return;
    this.turn = null;
    let reply = text;
    if (reply == null) reply = extractReply(t.before, (await this.historyText()) || this.lastText, t.prompt);
    const message = {
      role: 'assistant',
      text: reply || (stopped ? '（停止した）' : error ? '' : '（応答を画面から読み取れなかった。端末を確認）'),
      elapsedMs: Date.now() - t.startedAt, stopped, error: error || '',
    };
    this.setPhase(this.phase === 'dead' || this.phase === 'gone' ? this.phase : 'ready', '');
    t.done(message);
  }

  // 依頼を送ってターンを始める。応答は done(message) で戻す。
  async send(prompt, done) {
    if (this.turn) throw new Error('このセッションは応答中です');
    if (this.phase === 'dead' || this.phase === 'gone') throw new Error('CLI が終了しています。再起動してください');
    const text = String(prompt || '');
    const before = (await this.historyText()) || this.lastText;
    const lines = text.split('\n');
    if (lines.length === 1) {
      const r = await this.shell.run(cmdKeys(this.name, ['-l', '--', text]));
      if (!r.ok) throw new Error(`send-keys に失敗: ${r.error}`);
    } else {
      const buf = `agent-app-${Date.now().toString(36)}`;
      const r = await this.shell.run(`${TMUX} set-buffer -b ${sq(buf)} -- ${sq(text)} && ${TMUX} paste-buffer -p -d -b ${sq(buf)} -t ${sq(this.name)}`);
      if (!r.ok) throw new Error(`paste-buffer に失敗: ${r.error}`);
    }
    // 貼り付け直後の Enter は TUI に食われることがあるので少し待つ
    await sleep(350);
    const r2 = await this.shell.run(cmdKeys(this.name, ['--', 'Enter']));
    if (!r2.ok) throw new Error(`Enter を送れません: ${r2.error}`);
    this.turn = { prompt: text, startedAt: Date.now(), before, sawBusy: false, readyCount: 0, stopped: false, done };
    this.setPhase('busy', '応答中');
    this.schedule(0);
  }

  // 生成を止める。claude / codex は Esc、それ以外は C-c。
  async stop() {
    if (!this.turn) return false;
    this.turn.stopped = true;
    const key = this.patterns.busy && /esc/i.test(this.patterns.busy.source) ? 'Escape' : 'C-c';
    await this.shell.run(cmdKeys(this.name, ['--', key]));
    return true;
  }

  async keys(data) {
    for (const args of keysToArgs(data)) {
      const r = await this.shell.run(cmdKeys(this.name, args));
      if (!r.ok) throw new Error(r.error);
    }
    this.schedule(0);
  }

  async resize(cols, rows) {
    const c = Math.max(20, Math.min(400, Number(cols) || DEFAULT_COLS));
    const r = Math.max(5, Math.min(200, Number(rows) || DEFAULT_ROWS));
    if (c === this.cols && r === this.rows) return;
    this.cols = c; this.rows = r;
    await this.shell.run(cmdResize(this.name, c, r));
    this.sentOnce = false;
    this.schedule(0);
  }

  watch() { this.watchers += 1; this.sentOnce = false; this.schedule(0); }
  unwatch() { this.watchers = Math.max(0, this.watchers - 1); }

  // 追跡をやめる（tmux セッションは残す）
  detach() {
    this.closed = true;
    clearTimeout(this.timer);
    if (this.turn) this.finishTurn({ error: '追跡を終了しました' });
  }

  async kill() {
    this.detach();
    await this.shell.run(cmdKill(this.name));
    this.setPhase('gone', '');
  }
}

async function listSessions(shell) {
  const r = await shell.run(cmdList());
  return r.output.split('\n').map((s) => s.trim()).filter((s) => s.startsWith('agent-app-'));
}

module.exports = {
  SOCKET, TMUX, DEFAULT_COLS, DEFAULT_ROWS, DEFAULT_READY, ATTENTION,
  sessionName, isChrome, classify, compilePatterns, extractReply, keysToArgs,
  cmdHas, cmdNew, cmdScreen, parseScreen, cmdKeys, cmdKill, cmdResize, cmdList,
  Conversation, listSessions,
};
