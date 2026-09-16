'use strict';

// 触ってよい場所を決める 1 か所。userData と、登録リポジトリ・作業フォルダの解決はここだけが持つ。
// 画面から受け取るのはリポジトリのパスと作業フォルダの**名前**だけで、生のパスは受け取らない
// （設計書 ADR-2）。ipc とその周辺（共有の実行・タスクの教示）は、ここを通してからファイルへ触る。

const fs = require('fs');
const { app } = require('electron');
const store = require('./store');
const host = require('./host');
const worktree = require('./worktree');

function userData() { return app.getPath('userData'); }

// 触ってよいのは**登録したリポジトリだけ**。登録に無いパスは、実在していても断る。
function requireRepo(repo) {
  const dir = String(repo || '').trim();
  if (!dir) throw new Error('リポジトリを選んでください');
  if (!store.isRegistered(userData(), dir)) throw new Error('登録していないフォルダです');
  let st;
  try { st = fs.statSync(dir); } catch { st = null; }
  if (!st || !st.isDirectory()) throw new Error('フォルダが見つかりません');
  return dir;
}

function distroFor(repo) {
  return host.hostOf(repo, store.loadConfig(userData()).wslDistro).distro;
}

// 作業フォルダ。画面から受け取るのは worktree の**名前**だけで、生のパスは受け取らない
// （名前は worktree.checkName が形を検査するので `..` を持ち込めない）。
function dirsOf(repo, name, { mustExist = false } = {}) {
  const dirs = worktree.dirsFor(requireRepo(repo), name || '');
  if (mustExist && dirs.name) {
    let st;
    try { st = fs.statSync(dirs.fsDir); } catch { st = null; }
    if (!st || !st.isDirectory()) {
      throw new Error(`作業フォルダが見つかりません: ${worktree.SUBDIR}/${dirs.name}（この画面の外で消された可能性があります）`);
    }
  }
  return dirs;
}

function sessionDirs(sess) {
  return dirsOf(sess.repo, sess.worktree || '');
}

// 「ブランチ全体」の差分の分岐元。登録したフォルダ（＝ふつうは本体の worktree）の今のブランチ。
async function mainBranch(repo, distro) {
  const r = await host.shellFor(distro).exec(['git', '-C', host.toHostPath(repo), 'rev-parse', '--abbrev-ref', 'HEAD'], { timeoutMs: 20000 });
  const name = r.ok ? r.output.trim() : '';
  return name && name !== 'HEAD' ? name : '';
}

module.exports = { userData, requireRepo, distroFor, dirsOf, sessionDirs, mainBranch };
