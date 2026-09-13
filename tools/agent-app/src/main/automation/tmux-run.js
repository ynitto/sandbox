'use strict';

// 手動実行の1セッション経路。終了時に色付き履歴を保存してからペインを回収する。
const agentCli = require('../agentCli');
const host = require('../host');
const tmux = require('../tmux');

async function prepare({ root, agent, model = '', prompt, requestId, distro = '', transport,
  onScreen = () => {}, onExit = () => {} }, deps = {}) {
  const api = { load: agentCli.load, interactiveCmd: agentCli.interactiveCmd,
    hostOf: host.hostOf, probe: host.probe, Conversation: tmux.Conversation,
    compilePatterns: tmux.compilePatterns, ...deps };
  if (transport === 'headless') return { warning: '対話セッションが無効なため、実行ログを表示します。' };
  const spec = api.load(agent, root);
  if (!spec.interactive) return { warning: 'この AI は対話起動に対応していないため、実行ログを表示します。' };
  const target = api.hostOf(root, distro);
  const info = await api.probe(target.distro);
  if (!info.ok || !info.tmux) return { warning: `tmux を利用できないため、実行ログを表示します。${info.error || ''}` };
  const cmd = api.interactiveCmd(spec, { model, readonly: false, autoApprove: true, cliSession: '', history: [] });
  const env = Object.entries(cmd.env || {}).map(([name, value]) => `${name}=${value}`);
  let finished = false;
  let archive = null;
  let endReady;
  const ended = new Promise((resolve) => { endReady = resolve; });
  let paneReady;
  const opened = new Promise((resolve) => { paneReady = resolve; });
  const conv = new api.Conversation({
    id: `run-${requestId}`, shell: target.shell, cwd: target.cwd,
    argv: env.length ? ['env', ...env, ...cmd.argv] : cmd.argv,
    patterns: api.compilePatterns(spec.interactive),
    emit: (channel, payload) => { if (!finished && channel === 'term:screen') onScreen(payload); },
  });
  conv.watchers = 1;
  let stopped = false;
  let starting = null;
  async function finish(message) {
    if (finished) return;
    finished = true;
    paneReady(false);
    const error = stopped ? '実行を停止しました' : message.error || '';
    // -J で行を結合せず、表示時の折り返しと色を保った履歴を取得する。
    const captured = await conv.capture({ history: true, joinHistory: false }).catch(() => ({ ok: false }));
    const screen = captured.ok ? captured.screen : conv.lastScreen;
    if (screen) archive = { ...screen, lines: screen.text.split('\n'), offset: 0 };
    await conv.kill().catch(() => {});
    endReady();
    onExit({ code: error ? 1 : 0, stdout: message.text || '', stderr: error, truncated: false });
  }
  function archivedScreen() {
    if (!archive) return false;
    const bottom = Math.max(0, archive.lines.length - archive.rows);
    archive.offset = Math.max(0, Math.min(bottom, archive.offset));
    const start = bottom - archive.offset;
    const cursorY = Math.max(0, Math.min(archive.rows - 1,
      archive.lines.length - (archive.originalRows || archive.rows) + (archive.cursor?.y || 0) - start));
    onScreen({ id: conv.id, cols: archive.cols, rows: archive.rows,
      text: archive.lines.slice(start, start + archive.rows).join('\n'),
      cursor: { x: archive.cursor?.x || 0, y: cursorY }, scrollOffset: archive.offset });
    return archive.offset;
  }
  async function withPane(action) {
    // 画面は起動より先に表示される。サイズ変更・スクロール・入力はペインを作ってから通す。
    if (finished || !(await opened) || finished) return false;
    try { return await action(); } catch (error) {
      // 取得中に正常終了・停止でペインを回収した場合だけ、遅れて返ったエラーを捨てる。
      if (finished) return false;
      throw error;
    }
  }
  return {
    warning: [cmd.warning, cmd.readonlyWarning].filter(Boolean).join('\n'),
    async start() {
      if (finished) return;
      try {
        starting = conv.open({ reuse: false });
        await starting;
        if (stopped) { await conv.kill(); return; }
        paneReady(true);
        await conv.waitReady();
        if (stopped) return;
        await conv.send(prompt, finish);
      } catch (error) { await finish({ error: error.message }); }
    },
    async stop() {
      stopped = true;
      // open が完了する前に kill すると、その後でペインが作られて残ってしまう。
      if (starting) await starting.catch(() => {});
      await finish({});
      return true;
    },
    keys: (data) => withPane(() => conv.keys(data)),
    async resize(cols, rows) {
      if (!finished) return withPane(() => conv.resize(cols, rows));
      await ended;
      if (!archive) return false;
      archive.originalRows ||= archive.rows;
      archive.rows = Math.max(5, Math.min(200, Number(rows) || archive.rows));
      archive.cols = Math.max(20, Math.min(400, Number(cols) || archive.cols));
      return archivedScreen();
    },
    async scroll(lines) {
      if (!finished) return withPane(() => conv.scroll(lines));
      await ended;
      if (!archive) return false;
      archive.offset -= Math.trunc(Number(lines) || 0);
      return archivedScreen();
    },
  };
}

module.exports = { prepare };
