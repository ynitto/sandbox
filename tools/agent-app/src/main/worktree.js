'use strict';

// 会話ごとに作業フォルダを分ける（git worktree）。
//
// 同じリポジトリで並行して会話を進めると、1 つの作業ツリーを複数の CLI が同時に書き換えて
// 混ざる。worktree を生やせば、会話ごとに**別のフォルダ・別のブランチ**で作業でき、
// 本体（登録したフォルダ）はそのまま残る。
//
// 置き場は `<リポジトリ>/.worktrees/<名前>` の 1 か所に決め打つ。理由:
//   - 名前だけを保存すればよく、画面から生のパスを受け取らない（`..` を持ち込めない）
//   - Windows では git / tmux に渡すのは WSL 表記、fs で読むのは Windows 表記という
//     2 つの表記が要る。決め打ちなら「登録したパス + .worktrees + 名前」で両方を作れる
//     （git が返す WSL 表記を Windows 表記へ逆変換しなくてよい）
// この画面の外で作った worktree は一覧には出すが、会話の作業フォルダには選べない。
//
// git への書き込みはここだけ（worktree の追加・削除とブランチ作成）。変更ビューは読むだけ。

const path = require('path');
const host = require('./host');

const SUBDIR = '.worktrees';
const sq = host.sq;

// フォルダ名。パス区切りと `.` 始まりを弾く（`..` も同時に落ちる）。
const NAME_RE = /^[A-Za-z0-9][\w.@+-]{0,59}$/;

function checkName(name) {
  const n = String(name || '').trim();
  if (!NAME_RE.test(n)) throw new Error(`worktree の名前が不正です: ${name}`);
  return n;
}

// ブランチ名 → フォルダ名（`feature/foo` → `feature-foo`）。
function slug(branch) {
  const s = String(branch || '').trim().replace(/[^\w.@+-]+/g, '-').replace(/^[-.]+/, '').replace(/[-.]+$/, '').slice(0, 60);
  return s;
}

// 会話の作業フォルダ。name が空ならリポジトリ本体。
//   fsDir   … Node の fs で読む表記（登録したときのまま。Windows なら C:\… / \\wsl$\…）
//   hostDir … git / tmux へ渡す表記（Windows なら /mnt/c/… / /home/…）
function dirsFor(repo, name) {
  const n = name ? checkName(name) : '';
  const fsDir = n ? path.join(String(repo), SUBDIR, n) : String(repo);
  return { name: n, fsDir, hostDir: host.toHostPath(fsDir) };
}

function shellOf(repo, distro) {
  return { shell: host.shellFor(distro), cwd: host.toHostPath(repo) };
}

// `git worktree list --porcelain` は空行区切りのレコード列。
function parseList(text) {
  const items = [];
  let cur = null;
  for (const line of String(text || '').split('\n')) {
    const s = line.replace(/\r$/, '');
    if (!s.trim()) { if (cur) { items.push(cur); cur = null; } continue; }
    const at = s.indexOf(' ');
    const key = at < 0 ? s : s.slice(0, at);
    const val = at < 0 ? '' : s.slice(at + 1);
    if (key === 'worktree') { if (cur) items.push(cur); cur = { path: val, head: '', branch: '', detached: false, locked: '', prunable: '' }; continue; }
    if (!cur) continue;
    if (key === 'HEAD') cur.head = val;
    else if (key === 'branch') cur.branch = val.replace(/^refs\/heads\//, '');
    else if (key === 'detached') cur.detached = true;
    else if (key === 'locked') cur.locked = val || 'locked';
    else if (key === 'prunable') cur.prunable = val || 'prunable';
  }
  if (cur) items.push(cur);
  return items;
}

// 各 worktree の「変更の数」と「本体より何コミット進んでいるか」。
// 1 回のシェル呼び出しでまとめて撃つ（worktree ごとに起こすと Windows で目に見えて遅い）。
function statusScript(paths, mainHead) {
  return paths.map((p) => [
    `printf '\\036%s\\t' ${sq(p)}`,
    `printf '%s\\t' "$(git -C ${sq(p)} status --porcelain --untracked-files=all 2>/dev/null | wc -l)"`,
    mainHead
      ? `printf '%s\\n' "$(git -C ${sq(p)} rev-list --count ${sq(mainHead)}..HEAD 2>/dev/null || echo 0)"`
      : `printf '0\\n'`,
  ].join('; ')).join('; ');
}

function parseStatus(text) {
  const out = new Map();
  for (const chunk of String(text || '').split('\u001e')) {
    if (!chunk.trim()) continue;
    const [p, dirty, ahead] = chunk.split('\t');
    if (!p) continue;
    out.set(p, { dirty: parseInt(String(dirty).trim(), 10) || 0, ahead: parseInt(String(ahead).trim(), 10) || 0 });
  }
  return out;
}

// リポジトリの worktree 一覧。先頭は本体（main）。
// selectable … `<repo>/.worktrees/<名前>` にあり、会話の作業フォルダに選べるもの
async function list(repo, distro = '', { withStatus = true } = {}) {
  const { shell, cwd } = shellOf(repo, distro);
  const r = await shell.exec(['git', '-C', cwd, 'worktree', 'list', '--porcelain'], { timeoutMs: 20000 });
  if (!r.ok) return { items: [], root: '', error: r.error || r.output || 'git リポジトリではありません' };
  const root = host.joinHost(cwd, SUBDIR);
  const raw = parseList(r.output);
  const items = raw.map((w, i) => {
    const under = w.path.startsWith(`${root}/`) ? w.path.slice(root.length + 1) : '';
    const name = under && !under.includes('/') && NAME_RE.test(under) ? under : '';
    return { ...w, main: i === 0, name, selectable: i > 0 && !!name, dirty: 0, ahead: 0 };
  });
  if (withStatus && items.length) {
    const st = await shell.run(statusScript(items.map((w) => w.path), items[0].head), { timeoutMs: 30000 });
    const map = parseStatus(st.output);
    for (const w of items) Object.assign(w, map.get(w.path) || {});
  }
  return { items, root, error: '' };
}

async function find(repo, name, distro = '') {
  const res = await list(repo, distro, { withStatus: false });
  if (res.error) throw new Error(res.error);
  const hit = res.items.find((w) => w.name === name && w.selectable);
  if (!hit) throw new Error(`作業フォルダが見つかりません: ${SUBDIR}/${name}`);
  return hit;
}

// `.worktrees/` を **ローカルの除外**（.git/info/exclude）へ足す。
// リポジトリの .gitignore は触らない（コミットに混ざる）。これをしないと、本体の
// 変更ビューに worktree のフォルダが「新規」として並ぶ。
async function ensureExcluded(repo, distro = '') {
  const { shell, cwd } = shellOf(repo, distro);
  const line = `/${SUBDIR}/`;
  const script = [
    `d="$(git -C ${sq(cwd)} rev-parse --git-common-dir 2>/dev/null)" || exit 0`,
    `case "$d" in /*) ;; *) d=${sq(cwd)}/"$d" ;; esac`,
    `f="$d/info/exclude"`,
    `mkdir -p "$d/info" || exit 0`,
    `grep -qxF ${sq(line)} "$f" 2>/dev/null || printf '%s\\n' ${sq(line)} >> "$f"`,
  ].join('; ');
  await shell.run(script, { timeoutMs: 15000 });
}

// 作業フォルダを生やす。
//   branch … 作る（or 既にあれば使う）ブランチ名
//   base   … 分岐元（空なら現在の HEAD）
// 既にあるブランチはそのまま持ってくる（`-b` を付けない）。他の worktree で
// チェックアウト中のブランチは git が断るので、その理由をそのまま返す。
async function create(repo, { branch, base = '', name = '' } = {}, distro = '') {
  const br = String(branch || '').trim();
  if (!br) throw new Error('ブランチ名を入れてください');
  const wtName = checkName(name || slug(br));
  const { shell, cwd } = shellOf(repo, distro);

  const fmt = await shell.exec(['git', '-C', cwd, 'check-ref-format', `refs/heads/${br}`], { timeoutMs: 15000 });
  if (!fmt.ok) throw new Error(`ブランチ名として使えません: ${br}`);

  const baseRef = String(base || '').trim();
  if (baseRef) {
    const ok = await shell.exec(['git', '-C', cwd, 'rev-parse', '--verify', '--quiet', `${baseRef}^{commit}`], { timeoutMs: 15000 });
    if (!ok.ok) throw new Error(`分岐元が見つかりません: ${baseRef}`);
  }

  const dirs = dirsFor(repo, wtName);
  const exists = await shell.exec(['git', '-C', cwd, 'show-ref', '--verify', '--quiet', `refs/heads/${br}`], { timeoutMs: 15000 });
  await ensureExcluded(repo, distro);

  const argv = ['git', '-C', cwd, 'worktree', 'add'];
  if (exists.ok) argv.push(dirs.hostDir, br);                       // 既にあるブランチを持ってくる
  else argv.push('-b', br, dirs.hostDir, ...(baseRef ? [baseRef] : []));
  const r = await shell.exec(argv, { timeoutMs: 120000 });
  if (!r.ok) throw new Error(`worktree を作れません: ${(r.error || r.output || '').split('\n').slice(-4).join('\n')}`);
  return { name: wtName, branch: br, path: dirs.hostDir, reusedBranch: exists.ok };
}

// 片付ける。中に変更が残っていると git が断る（force で押し切れる）。
// ブランチの削除は既定で安全側（`-d`＝マージ済みだけ）。
async function remove(repo, name, { force = false, deleteBranch = false, forceBranch = false } = {}, distro = '') {
  const wt = await find(repo, checkName(name), distro);
  const { shell, cwd } = shellOf(repo, distro);
  const argv = ['git', '-C', cwd, 'worktree', 'remove', ...(force ? ['--force'] : []), wt.path];
  const r = await shell.exec(argv, { timeoutMs: 60000 });
  if (!r.ok) {
    const detail = (r.error || r.output || '').split('\n').slice(-3).join('\n');
    throw new Error(/contains modified or untracked|is dirty/i.test(detail)
      ? `作業フォルダに未コミットの変更が残っています（「変更ごと削除」で押し切れます）\n${detail}`
      : `worktree を削除できません: ${detail}`);
  }
  const out = { removed: true, branch: wt.branch, branchRemoved: false, branchError: '' };
  if (deleteBranch && wt.branch) {
    const b = await shell.exec(['git', '-C', cwd, 'branch', forceBranch ? '-D' : '-d', wt.branch], { timeoutMs: 30000 });
    out.branchRemoved = b.ok;
    if (!b.ok) out.branchError = (b.error || b.output || '').split('\n').slice(-2).join('\n');
  }
  return out;
}

module.exports = { SUBDIR, NAME_RE, checkName, slug, dirsFor, parseList, parseStatus, statusScript, list, find, create, remove, ensureExcluded };
