'use strict';

// 端末ミラー。main が tmux capture-pane で取った画面（色付き）を xterm に描き、
// キー入力を tmux send-keys へ返す。xterm は「表示とキーボード」だけで、
// 端末の状態（スクロールバック・カーソル）は tmux 側が正。
(function initTerm() {
  const state = { id: '', term: null, fit: null, host: null, ro: null, cols: 120, rows: 36, lastSize: '', screenSeq: 0 };

  function ensure(hostEl) {
    if (state.term) return;
    const term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Noto Sans Mono CJK JP", monospace',
      fontSize: 12, lineHeight: 1.15, cursorBlink: false, scrollback: 0, convertEol: false, allowProposedApi: true,
      theme: { background: '#0b0f14', foreground: '#d8dee9', cursor: '#88c0d0', selectionBackground: '#3b4252' },
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(hostEl);
    term.onData((data) => { if (state.id) api.termKeys(state.id, data).catch(() => {}); });
    // xterm は 1 画面分（scrollback 0）。ホイールは tmux のコピーモードへ流さず、単に無視する。
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
    out.push(`\x1b[${(p.cursor ? p.cursor.y : 0) + 1};${(p.cursor ? p.cursor.x : 0) + 1}H\x1b[?25h`);
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
    state.term.focus();
  }

  function detach() {
    if (state.id) api.termUnwatch(state.id).catch(() => {});
    state.id = '';
    if (state.term) state.term.reset();
  }

  function size() { return { cols: state.cols, rows: state.rows }; }
  function focus() { if (state.term) state.term.focus(); }

  window.Term = { attach, detach, applyScreen, refit, size, focus, current: () => state.id };
})();
