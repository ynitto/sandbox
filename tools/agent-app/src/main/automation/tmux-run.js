'use strict';

// 手動実行の1セッション経路。最初の依頼が終わったら実行の結果を返し、ペインは残して
// 同じセッションへ続けて依頼できるようにする（会話画面と同じく、tmux が生きている限り CLI が
// 文脈を保つ）。閉じるのは次の実行・停止・アプリの終了・CLI 自身の終了のとき。閉じる前に
// 色付き履歴を保存し、閉じた後のスクロールはその履歴で返す。
const agentCli = require('../agentCli');
const host = require('../host');
const tmux = require('../tmux');

async function prepare({ root, agent, model = '', prompt, requestId, distro = '', transport,
  onScreen = () => {}, onExit = () => {}, onSession = () => {} }, deps = {}) {
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
  let finished = false;                  // 最初の依頼が終わった（実行の結果は返した）
  let closed = false;                    // ペインを回収した
  let archive = null;
  let endReady;
  const ended = new Promise((resolve) => { endReady = resolve; });
  let paneReady;
  const opened = new Promise((resolve) => { paneReady = resolve; });
  const conv = new api.Conversation({
    id: `run-${requestId}`, shell: target.shell, cwd: target.cwd,
    argv: env.length ? ['env', ...env, ...cmd.argv] : cmd.argv,
    patterns: api.compilePatterns(spec.interactive),
    emit: (channel, payload) => {
      if (closed) return;
      if (channel === 'term:screen') onScreen(payload);
      // 実行の後に CLI が自分で終わったら、続きは送れないので閉じる。
      if (channel === 'term:phase' && finished && ['dead', 'gone'].includes(payload.phase)) close().catch(() => {});
    },
  });
  conv.watchers = 1;
  let stopped = false;
  let starting = null;
  let followUp = null;                   // 続けて送った依頼の応答待ち
  async function close() {
    if (closed) return;
    closed = true;
    paneReady(false);
    // -J で行を結合せず、表示時の折り返しと色を保った履歴を取得する。
    const captured = await conv.capture({ history: true, joinHistory: false }).catch(() => ({ ok: false }));
    const screen = captured.ok ? captured.screen : conv.lastScreen;
    if (screen) archive = { ...screen, lines: screen.text.split('\n'), offset: 0 };
    await conv.kill().catch(() => {});
    endReady();
    if (finished) onSession({ phase: 'closed' });
  }
  async function finish(message, { keep = false } = {}) {
    if (finished) return;
    finished = true;
    const error = stopped ? '実行を停止しました' : message.error || '';
    const dead = ['dead', 'gone'].includes(conv.phase);
    const session = keep && !stopped && !dead;
    if (!session) {
      await close();
    }
    onExit({ code: error ? 1 : 0, stdout: message.text || '', stderr: error, truncated: false, session });
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
    if (closed || !(await opened) || closed) return false;
    try { return await action(); } catch (error) {
      // 取得中に停止・終了でペインを回収した場合だけ、遅れて返ったエラーを捨てる。
      if (closed) return false;
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
        await conv.send(prompt, (message) => finish(message, { keep: true }));
      } catch (error) { await finish({ error: error.message }); }
    },
    // 実行が終わった後、同じセッションへ続けて依頼する。応答が終わると onSession で返す。
    async followUp(text) {
      const body = String(text || '').trim();
      if (!body) throw new Error('依頼を入力してください');
      if (!finished || closed) throw new Error('続けて依頼できるセッションがありません');
      if (followUp) throw new Error('このセッションは応答中です');
      followUp = true;
      try {
        await conv.send(body, (message) => {
          followUp = null;
          if (!closed) onSession({ phase: 'ready', text: message.text || '', error: message.error || '', stopped: !!message.stopped });
        });
      } catch (error) { followUp = null; throw error; }
      onSession({ phase: 'busy' });
      return true;
    },
    // 続けて送った依頼の応答だけを止める。セッションは残す。
    interrupt: () => (followUp && !closed ? conv.stop() : false),
    alive: () => finished && !closed,
    close,
    async stop() {
      if (finished) { await close(); return true; }
      stopped = true;
      // open が完了する前に kill すると、その後でペインが作られて残ってしまう。
      if (starting) await starting.catch(() => {});
      await finish({});
      return true;
    },
    keys: (data) => withPane(() => conv.keys(data)),
    async resize(cols, rows) {
      if (!closed) return withPane(() => conv.resize(cols, rows));
      await ended;
      if (!archive) return false;
      archive.originalRows ||= archive.rows;
      archive.rows = Math.max(5, Math.min(200, Number(rows) || archive.rows));
      archive.cols = Math.max(20, Math.min(400, Number(cols) || archive.cols));
      return archivedScreen();
    },
    async scroll(lines) {
      if (!closed) return withPane(() => conv.scroll(lines));
      await ended;
      if (!archive) return false;
      archive.offset -= Math.trunc(Number(lines) || 0);
      return archivedScreen();
    },
  };
}

module.exports = { prepare };
