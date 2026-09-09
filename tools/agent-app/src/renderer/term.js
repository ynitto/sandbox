'use strict';

// 端末ミラー。main が tmux capture-pane で取った画面（色付き）を xterm に描き、
// キー入力を tmux send-keys へ返す。xterm は「表示とキーボード」だけで、
// 端末の状態（スクロールバック・カーソル）は tmux 側が正。
//
// 画面には端末ミラーが 2 つある（会話と、タスクを AI と作る会話）。それぞれが自分の
// 会話 ID と xterm を持つので、createTerm() で作った実体を別々に持つ。
(function initTerm() {
function createTerm() {
  const state = {
    id: '', term: null, fit: null, host: null, ro: null, cols: 120, rows: 36, lastSize: '', screenSeq: 0,
    inputEnabled: false, onFocus: null, onAccepted: null, onError: null, onEscape: null,
  };

  function color(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function theme() {
    return {
      background: color('--term-bg', '#0b0f14'), foreground: color('--term-text', '#d8dee9'),
      cursor: color('--term-cursor', '#7dd3fc'), selectionBackground: color('--term-selection', '#334155'),
      black: color('--term-black', '#1f2937'), red: color('--term-red', '#f87171'),
      green: color('--term-green', '#4ade80'), yellow: color('--term-yellow', '#facc15'),
      blue: color('--term-blue', '#60a5fa'), magenta: color('--term-magenta', '#c084fc'),
      cyan: color('--term-cyan', '#22d3ee'), white: color('--term-white', '#e5e7eb'),
      brightBlack: '#64748b', brightRed: '#fca5a5', brightGreen: '#86efac', brightYellow: '#fde047',
      brightBlue: '#93c5fd', brightMagenta: '#d8b4fe', brightCyan: '#67e8f9', brightWhite: '#f8fafc',
    };
  }

  async function sendData(data) {
    if (!state.id || !state.inputEnabled) return false;
    if (data === '\x1b' && state.onEscape && !state.onEscape()) return false;
    try {
      await api.termKeys(state.id, data);
      if (state.onAccepted) state.onAccepted(data);
      return true;
    } catch (error) {
      if (state.onError) state.onError(error);
      return false;
    }
  }

  function ensure(hostEl) {
    if (state.term) return;
    const term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Noto Sans Mono CJK JP", monospace',
      fontSize: 12, lineHeight: 1.2, cursorBlink: false, scrollback: 0, convertEol: false, allowProposedApi: true,
      theme: theme(),
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(hostEl);
    term.onData((data) => { sendData(data); });
    hostEl.addEventListener('pointerup', () => {
      if (!term.hasSelection() && state.onFocus) state.onFocus();
    });
    // xterm 自身には履歴を二重保持せず、ホイール量を tmux 履歴の表示オフセットへ渡す。
    // hostEl の wheel リスナーでは xterm 自身の既定ホイール処理（scrollback:0 なので何も
    // 動かない）を止められず、tmux へ届く前に握り潰されていた。xterm の
    // attachCustomWheelEventHandler で先に受け取り、false を返して既定処理を止める。
    // message 入力モードでも端末の閲覧はできるよう inputEnabled では制限しない。
    term.attachCustomWheelEventHandler((event) => {
      if (!state.id || !event.deltaY) return true;
      event.preventDefault();
      const direction = event.deltaY < 0 ? -1 : 1;
      const lines = direction * Math.max(1, Math.min(state.rows, Math.ceil(Math.abs(event.deltaY) / 30)));
      api.termScroll(state.id, lines).catch((error) => { if (state.onError) state.onError(error); });
      return false;
    });
    state.term = term; state.fit = fit; state.host = hostEl;
    state.ro = new ResizeObserver(() => refit());
    state.ro.observe(hostEl);
  }

  function refit() {
    if (!state.term || !state.host || state.host.clientHeight < 40 || state.host.offsetParent === null) return;
    try { state.fit.fit(); } catch { return; }
    const size = `${state.term.cols}x${state.term.rows}`;
    if (size === state.lastSize) return;
    state.lastSize = size;
    state.cols = state.term.cols; state.rows = state.term.rows;
    if (state.id) api.termResize(state.id, state.cols, state.rows).catch(() => {});
  }

  // 画面を丸ごと描き直す。カーソルは tmux の位置へ。
  function applyScreen(p) {
    if (!state.term || p.id !== state.id) return;
    const term = state.term;
    const lines = String(p.text || '').split('\n');
    const out = [`\x1b[?25l\x1b[H\x1b[0m`];
    for (let i = 0; i < Math.max(lines.length, 1); i += 1) {
      out.push(`\x1b[${i + 1};1H\x1b[2K`);
      out.push(lines[i] || '');
      out.push('\x1b[0m');
    }
    for (let i = lines.length; i < term.rows; i += 1) out.push(`\x1b[${i + 1};1H\x1b[2K`);
    if (p.scrollOffset > 0) out.push('\x1b[?25l');
    else out.push(`\x1b[${(p.cursor ? p.cursor.y : 0) + 1};${(p.cursor ? p.cursor.x : 0) + 1}H\x1b[?25h`);
    term.write(out.join(''));
  }

  // 会話 ID を切り替える。前の会話の監視は外し、新しい会話を監視する。
  async function attach(id, hostEl) {
    ensure(hostEl);
    if (state.id && state.id !== id) api.termUnwatch(state.id).catch(() => {});
    state.id = id || '';
    state.term.reset();
    if (!id) return;
    refit();
    await api.termWatch(id).catch(() => {});
  }

  function detach() {
    if (state.id) api.termUnwatch(state.id).catch(() => {});
    state.id = '';
    if (state.term) state.term.reset();
  }

  function size() { return { cols: state.cols, rows: state.rows }; }
  function focus() { if (state.term) state.term.focus(); }

  function setInputEnabled(enabled) {
    state.inputEnabled = !!enabled;
    if (state.term) state.term.options.cursorBlink = !!enabled;
  }

  function configure(handlers = {}) {
    state.onFocus = handlers.onFocus || null;
    state.onAccepted = handlers.onAccepted || null;
    state.onError = handlers.onError || null;
    state.onEscape = handlers.onEscape || null;
  }

  return {
    attach, detach, applyScreen, refit, size, focus, sendKey: sendData, setInputEnabled, configure,
    current: () => state.id,
  };
}

  window.createTerm = createTerm;
  window.Term = createTerm();          // 会話の端末ミラー
  window.TaskTerm = createTerm();      // タスクを AI と作る会話の端末ミラー
})();
