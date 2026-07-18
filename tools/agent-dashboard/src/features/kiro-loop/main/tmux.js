'use strict';

const crypto = require('crypto');
const exec = require('./exec');

function pathDigest(linuxPath) {
  return crypto.createHash('sha1').update(String(linuxPath || '')).digest('hex').slice(0, 8);
}

function normalizeLinuxPath(p) {
  const s = exec.toWslCwd(p || '');
  if (!s) return '';
  // 末尾スラッシュを揃えて照合を安定させる
  return s.replace(/\/+$/, '') || '/';
}

function listTmuxSessions(prefix, distro = '') {
  const r = exec.shInWsl('tmux list-sessions -F "#{session_name}" 2>/dev/null || true', 8000, distro);
  if (!r.ok && !r.stdout) {
    return { ok: false, sessions: [], error: r.stderr || r.error || 'tmux list-sessions に失敗しました' };
  }
  const pref = String(prefix || 'kiro');
  const sessions = r.stdout
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s && s.startsWith(pref));
  return { ok: true, sessions, error: '' };
}

// tmux -F へ渡す区切りは**本物のタブ文字**でなければならない。ソースに '\\t'
// （バックスラッシュ + t の 2 文字）と書くと、シェルの二重引用符も tmux フォーマットも
// これをタブに変換せず、出力がタブで split できずペイン解析が全滅する（端末が
// 表示できなかった不具合の一因）。TAB 定数を埋め込んで事故を防ぐ。
const TAB = '\t';

function paneMeta(session, distro = '') {
  // 先頭ペインの cwd と pane_id。マルチペイン時は list で詳細を取る。
  const r = exec.shInWsl(
    `tmux list-panes -t ${exec.shellQuote(session)} -F "#{pane_id}${TAB}#{pane_current_path}${TAB}#{pane_title}${TAB}#{pane_active}" 2>/dev/null || true`,
    8000,
    distro
  );
  if (!r.stdout.trim()) return [];
  return r.stdout.split(/\r?\n/).filter(Boolean).map((line) => {
    const [paneId, cwd, title, active] = line.split(TAB);
    return {
      paneId: paneId || '',
      cwd: cwd || '',
      title: title || '',
      active: active === '1',
    };
  }).filter((p) => p.paneId.startsWith('%'));
  // ↑ sh -lc（ログインシェル）のプロファイル出力（nvm 等）が stdout に混入することが
  //   あるため、tmux のペイン行（%N 開始）だけを受け取る。
}

// tmux サーバ上の全ペイン（全セッション横断）。pane_id はサーバ全体で一意なので、
// セッション名が分からなくても pane から session を引ける。
function allPanes(distro = '') {
  const r = exec.shInWsl(
    `tmux list-panes -a -F "#{pane_id}${TAB}#{session_name}${TAB}#{pane_current_path}${TAB}#{pane_title}${TAB}#{pane_active}" 2>/dev/null || true`,
    8000,
    distro
  );
  const out = new Map();
  for (const line of String(r.stdout || '').split(/\r?\n/)) {
    if (!line.trim()) continue;
    const [paneId, session, cwd, title, active] = line.split(TAB);
    if (!paneId || !paneId.startsWith('%')) continue; // プロファイル出力等のノイズ行を除外
    out.set(paneId, {
      paneId,
      session: session || '',
      cwd: cwd || '',
      title: title || '',
      active: active === '1',
    });
  }
  return out;
}

// kiro-loop デーモンの状態ファイル（~/.kiro/loop-state/*.json。agent-loop クローンは
// ~/.agent/loop-state/*.json — 同じレコード形式なので両方読む）。
// { pid, cwd, sessions: [{ name, id, pane, alive }] } の配列。
// デーモンを tmux セッションの中で起動すると、ワーカーペインは「人のセッション」
// （名前は任意）内に分割で作られ、セッション名（kiro-loop-…）では見つけられない。
// 状態ファイルの pane_id 直参照ならセッション名に依存せず視聴できる。
function readLoopStates(distro = '') {
  const r = exec.shInWsl(
    'for f in "$HOME"/.kiro/loop-state/*.json "$HOME"/.agent/loop-state/*.json; do [ -f "$f" ] || continue; printf "\\036"; cat "$f"; done 2>/dev/null || true',
    8000,
    distro
  );
  if (!r.stdout) return [];
  const out = [];
  for (const chunk of r.stdout.split('\u001e')) {
    const t = chunk.trim();
    if (!t) continue;
    try {
      const data = JSON.parse(t);
      if (data && typeof data === 'object') out.push(data);
    } catch { /* 壊れた・書きかけの状態ファイルはスキップ */ }
  }
  return out;
}

function repoMatchesCwd(want, cwd) {
  if (!want) return true;
  if (!cwd) return false;
  return cwd === want || cwd.startsWith(`${want}/`);
}

function listSessions({ repo, prefix } = {}) {
  const distro = exec.wslDistro(repo || '');
  const want = normalizeLinuxPath(repo);
  const digest = want ? pathDigest(want) : '';
  const items = [];
  const seenTargets = new Set();
  const seenSessions = new Set();

  // 1) kiro-loop 状態ファイル由来のペイン（tmux 内で起動されたデーモンでも見つかる）
  const states = readLoopStates(distro);
  const panes = states.length ? allPanes(distro) : new Map();
  for (const st of states) {
    const stateCwd = normalizeLinuxPath(st.cwd || '');
    for (const s of Array.isArray(st.sessions) ? st.sessions : []) {
      const pane = s && s.pane ? panes.get(String(s.pane)) : null;
      if (!pane) continue; // ペインが消えている（dead / 状態ファイルが古い）
      const cwd = normalizeLinuxPath(pane.cwd || '') || stateCwd;
      if (!repoMatchesCwd(want, cwd) && !repoMatchesCwd(want, stateCwd)) continue;
      if (seenTargets.has(pane.paneId)) continue;
      seenTargets.add(pane.paneId);
      seenSessions.add(pane.session);
      items.push({
        session: pane.session,
        target: pane.paneId,
        name: String(s.name || s.id || ''),
        cwd,
        panes: [pane],
        alive: true,
      });
    }
  }

  // 2) セッション名（接頭辞）由来 — スタンドアロン起動（kiro / kiro-loop-<digest>-…）向け
  const listed = listTmuxSessions(prefix, distro);
  if (!listed.ok && !listed.sessions.length && !items.length) {
    return { ok: false, items: [], error: listed.error };
  }
  for (const session of listed.sessions) {
    if (seenSessions.has(session)) continue;
    const sessionPanes = paneMeta(session, distro);
    const primary = sessionPanes.find((p) => p.active) || sessionPanes[0] || null;
    const cwd = primary ? normalizeLinuxPath(primary.cwd) : '';
    const matchRepo = !want
      || (digest && session.includes(digest))
      || repoMatchesCwd(want, cwd);
    if (!matchRepo) continue;
    const target = (primary && primary.paneId) || session;
    if (seenTargets.has(target)) continue;
    seenTargets.add(target);
    items.push({
      session,
      target,
      name: '',
      cwd,
      panes: sessionPanes,
      alive: true,
    });
  }
  return { ok: true, items, error: '' };
}

function capture({ target, lines, repo } = {}) {
  const t = String(target || '').trim();
  if (!t) return { ok: false, text: '', error: 'target が空です' };
  const distro = exec.wslDistro(repo || '');
  const hist = Math.max(0, Math.min(Number(lines) || 0, 5000));
  // -e は SGR を残す（将来 xterm 向け）。A では UI 側で制御文字を軽く落とす。
  const args = hist > 0
    ? `tmux capture-pane -p -e -J -S -${hist} -t ${exec.shellQuote(t)}`
    : `tmux capture-pane -p -e -J -t ${exec.shellQuote(t)}`;
  const r = exec.shInWsl(`${args} 2>/dev/null`, 8000, distro);
  if (!r.ok && !r.stdout) {
    return { ok: false, text: '', error: r.stderr || r.error || 'capture-pane に失敗しました' };
  }
  return { ok: true, text: r.stdout, error: '' };
}

module.exports = {
  pathDigest, normalizeLinuxPath, listTmuxSessions, listSessions, capture, paneMeta,
  allPanes, readLoopStates,
};
