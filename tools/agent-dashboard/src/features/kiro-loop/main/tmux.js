'use strict';

const crypto = require('crypto');
const exec = require('./exec');

function pathDigest(linuxPath) {
  return crypto.createHash('sha1').update(String(linuxPath || '')).digest('hex').slice(0, 8);
}

function normalizeLinuxPath(p) {
  const s = exec.wslPath(p || '');
  if (!s) return '';
  // 末尾スラッシュを揃えて照合を安定させる
  return s.replace(/\/+$/, '') || '/';
}

function listTmuxSessions(prefix) {
  const r = exec.shInWsl('tmux list-sessions -F "#{session_name}" 2>/dev/null || true');
  if (!r.ok && !r.stdout) {
    return { ok: false, sessions: [], error: r.stderr || r.error || 'tmux list-sessions に失敗しました' };
  }
  const pref = String(prefix || 'kiro-loop-');
  const sessions = r.stdout
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s && s.startsWith(pref));
  return { ok: true, sessions, error: '' };
}

function paneMeta(session) {
  // 先頭ペインの cwd と pane_id。マルチペイン時は list で詳細を取る。
  const r = exec.shInWsl(
    `tmux list-panes -t ${exec.shellQuote(session)} -F "#{pane_id}\\t#{pane_current_path}\\t#{pane_title}\\t#{pane_active}" 2>/dev/null || true`
  );
  if (!r.stdout.trim()) return [];
  return r.stdout.split(/\r?\n/).filter(Boolean).map((line) => {
    const [paneId, cwd, title, active] = line.split('\t');
    return {
      paneId: paneId || '',
      cwd: cwd || '',
      title: title || '',
      active: active === '1',
    };
  });
}

function listSessions({ repo, prefix } = {}) {
  const listed = listTmuxSessions(prefix);
  if (!listed.ok && !listed.sessions.length) {
    return { ok: false, items: [], error: listed.error };
  }
  const want = normalizeLinuxPath(repo);
  const digest = want ? pathDigest(want) : '';
  const items = [];
  for (const session of listed.sessions) {
    const panes = paneMeta(session);
    const primary = panes.find((p) => p.active) || panes[0] || null;
    const cwd = primary ? normalizeLinuxPath(primary.cwd) : '';
    const matchRepo = !want
      || (digest && session.includes(digest))
      || (cwd && (cwd === want || cwd.startsWith(`${want}/`)));
    if (!matchRepo) continue;
    items.push({
      session,
      target: (primary && primary.paneId) || session,
      cwd,
      panes,
      alive: true,
    });
  }
  return { ok: true, items, error: '' };
}

function capture({ target, lines } = {}) {
  const t = String(target || '').trim();
  if (!t) return { ok: false, text: '', error: 'target が空です' };
  const hist = Math.max(0, Math.min(Number(lines) || 0, 5000));
  // -e は SGR を残す（将来 xterm 向け）。A では UI 側で制御文字を軽く落とす。
  const args = hist > 0
    ? `tmux capture-pane -p -e -J -S -${hist} -t ${exec.shellQuote(t)}`
    : `tmux capture-pane -p -e -J -t ${exec.shellQuote(t)}`;
  const r = exec.shInWsl(`${args} 2>/dev/null`);
  if (!r.ok && !r.stdout) {
    return { ok: false, text: '', error: r.stderr || r.error || 'capture-pane に失敗しました' };
  }
  return { ok: true, text: r.stdout, error: '' };
}

module.exports = {
  pathDigest, normalizeLinuxPath, listTmuxSessions, listSessions, capture, paneMeta,
};
