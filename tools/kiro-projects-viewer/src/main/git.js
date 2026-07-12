'use strict';

// kiro-project / kiro-flow の状態を共有する git リポジトリとの同期層。
//   pull      … 設定間隔（既定 300 秒・下限 60 秒）で律速した取り込み。
//   commitPush … ユーザー操作（指示・投入・記入・削除）の都度反映。
// kiro-project 本体の StateGit / kiro-flow の GitBus と同じ護りで、
// 同一クローンへコミットする他プロセス（本体の state_git・git-file-sync 等）と
// 共存できるようにする:
//   ・ステージは操作したディレクトリの pathspec だけ（add -A -- <dir>）。
//     他プロセスがステージした無関係な変更を自分のコミットに巻き込まない
//   ・push 競合は pull --rebase --autostash → 再 push の指数バックオフで吸収し、
//     force push は決してしない（他者のコミットを壊さない）。--autostash は他プロセスの
//     未コミット変更で作業ツリーが汚れていても rebase を走らせるため（退避→復帰で巻き込まない）
//   ・ロック起因の失敗（index.lock 等）はリトライし、30 秒以上古い残骸は自己回復
//   ・自プロセス内はリポジトリ単位の直列化キューで排他（pull と push を重ねない）

const { execFile } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 同期しない実行時データ（プロジェクトルート直下の名前）。kiro-flow の bus は run ごとに
// claims / events / results / artifacts を数百ファイル生む実行記録で、履歴に残す価値がない。
// 状態（charter / charters / backlog / needs / decisions / journal 等）だけを同期する。
const RUNTIME_DIRS = new Set(['bus', 'claims', 'flow-archive']);

// 自動 pull の下限間隔。設定でこれより短くしてもリモートへは詰めない
const MIN_INTERVAL_SEC = 60;
// ロック起因の git 失敗の再試行回数（1,2,4 秒バックオフ）
const LOCK_RETRIES = 4;
// これ以上古い .git 直下のロックは異常終了の残骸とみなして自己回復する
const LOCK_STALE_SEC = 30;
// push 競合（non fast-forward）の再試行回数（2,4 秒バックオフ）
const PUSH_RETRIES = 3;

const lastRemoteAt = new Map(); // toplevel -> リモートへ触れた最終時刻 epoch ms（失敗も含む）
const queues = new Map(); // toplevel -> 直列化キューの末尾 Promise

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function gitOnce(args, timeoutMs) {
  return new Promise((resolve) => {
    execFile(
      'git',
      args,
      {
        timeout: timeoutMs,
        env: {
          ...process.env,
          GIT_TERMINAL_PROMPT: '0', // 資格情報プロンプトで固まらせない
          GIT_EDITOR: 'true', // rebase がエディタを開かないように
          LC_ALL: 'C', // ロック競合の検知は英語メッセージの文字列マッチに頼る
        },
      },
      (err, stdout, stderr) => {
        resolve({
          code: err ? (typeof err.code === 'number' ? err.code : 1) : 0,
          out: String(stdout || '').trim(),
          err: String(stderr || (err ? err.message : '')).trim(),
        });
      }
    );
  });
}

function isLockError(res) {
  const e = res.err || '';
  return e.includes('.lock') && (e.includes('File exists') || /another git process/i.test(e));
}

const STALE_LOCKS = ['index.lock', 'HEAD.lock', 'config.lock', 'shallow.lock', 'packed-refs.lock'];

function removeStaleLocks(toplevel) {
  let removed = 0;
  const now = Date.now();
  for (const name of STALE_LOCKS) {
    const p = path.join(toplevel, '.git', name);
    try {
      const st = fs.statSync(p);
      if (st.isFile() && now - st.mtimeMs >= LOCK_STALE_SEC * 1000) {
        fs.unlinkSync(p);
        removed++;
      }
    } catch {
      /* 無い・消せないのは無視 */
    }
  }
  return removed;
}

// ロック起因の失敗だけリトライする git 実行。それ以外の失敗はそのまま返す
async function git(toplevel, args, timeoutMs = 60000) {
  let res;
  for (let i = 0; i < LOCK_RETRIES; i++) {
    res = await gitOnce(['-C', toplevel, ...args], timeoutMs);
    if (res.code === 0 || !isLockError(res)) return res;
    if (removeStaleLocks(toplevel) === 0 && i < LOCK_RETRIES - 1) {
      await sleep(1000 * 2 ** i);
    }
  }
  return res;
}

function fail(res, what) {
  return new Error(`${what} が失敗しました: ${(res.err || res.out || '').slice(-400)}`);
}

async function toplevelOf(dir) {
  const res = await gitOnce(['-C', dir, 'rev-parse', '--show-toplevel'], 10000);
  if (res.code !== 0) throw fail(res, 'git rev-parse');
  return res.out;
}

// リポジトリ単位の直列化: pull / commitPush を同じリポジトリで重ねて走らせない
function enqueue(toplevel, fn) {
  const tail = queues.get(toplevel) || Promise.resolve();
  const p = tail.then(fn, fn); // 前段の失敗は伝播させず順番だけ守る
  queues.set(
    toplevel,
    p.catch(() => {})
  );
  return p;
}

// origin より先行しているローカルコミット数（未 push の有無）
async function aheadCount(toplevel) {
  const res = await git(toplevel, ['rev-list', '--count', '@{u}..HEAD'], 10000);
  if (res.code !== 0) return 0; // upstream 未設定などは「押し出すものなし」扱い
  return parseInt(res.out, 10) || 0;
}

// 追跡ブランチ（origin/main 等）。push はこれを明示して行う — ローカルブランチ名が
// リモートと違う clone や push.default の設定に依存しないため
async function upstreamOf(toplevel) {
  const res = await git(
    toplevel,
    ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'],
    10000
  );
  if (res.code !== 0) return null;
  const m = res.out.match(/^([^/]+)\/(.+)$/);
  return m ? { remote: m[1], branch: m[2] } : null;
}

// pull 本体。取り込みは fast-forward のみで行い、作業ツリーが汚れているときは見送る。
// （rebase 引数はもう見ない — 呼び出し側の設定は残しているが、rebase/autostash は下記の理由で
//  この作業ツリーには使えない。）
async function doPull(toplevel, _rebase) {
  lastRemoteAt.set(toplevel, Date.now()); // 失敗しても間隔は空ける（リモートへの連打を防ぐ）

  // 作業ツリーが汚れているなら pull を見送る。
  //
  // 以前は --rebase --autostash で「汚れていても進める」ようにしていた。しかしこの作業ツリー
  // では kiro-project 本体が watch 中 5 秒ごとに状態ファイル（project.json / journal.md /
  // run-log.jsonl / status.json）を書き換え続けている。autostash がそれを退避している最中にも
  // 本体は書き込むため、復帰時にコンフリクトし、`<<<<<<< Updated upstream` が状態ファイルへ
  // 書き込まれて壊れた（project.json が JSON として読めなくなり、本体が状態を失った）。
  // 人が編集中の変更も同じ理由で巻き込まれる。
  //
  // pull は作業ツリーを書き換える操作なので、他者が書き込んでいる最中に無理に走らせない。
  // 静かになってから取り込む（次のポーリングで再挑戦する）。
  const dirty = await git(toplevel, ['status', '--porcelain'], 30000);
  if (dirty.code === 0 && dirty.out.trim()) {
    return { skipped: true, toplevel, dirty: true };
  }
  const res = await git(toplevel, ['pull', '--ff-only'], 120000);
  if (res.code !== 0) {
    throw fail(res, 'git pull --ff-only');
  }
  return { skipped: false, toplevel, output: res.out.slice(-400) };
}

// dir を含む git リポジトリを pull する。
//   force=false（ポーリングからの自動）… intervalSec 内はスキップ。
//     git リポジトリでない場合も黙ってスキップ（{skipped, notRepo}）。
//   force=true（手動ボタン）… 間隔を無視して実行。リポジトリでなければエラー。
async function pull(dir, { intervalSec = 300, force = false, rebase = false } = {}) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch (err) {
    if (!force) return { skipped: true, notRepo: true };
    throw new Error(`git リポジトリではありません: ${dir}（${err.message}）`);
  }
  const min = Math.max(MIN_INTERVAL_SEC, Number(intervalSec) || 0);
  const elapsed = (Date.now() - (lastRemoteAt.get(top) || 0)) / 1000;
  if (!force && elapsed < min) {
    return { skipped: true, toplevel: top, nextInSec: Math.ceil(min - elapsed) };
  }
  return enqueue(top, () => doPull(top, rebase));
}

// dir の「今」の内容を worktree 側の同じ相対パスへ反映する（削除も反映するため一度消す）。
// 実行記録（RUNTIME_DIRS）は持ち込まない＝履歴に入れない。
function syncInto(src, dest) {
  fs.rmSync(dest, { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (RUNTIME_DIRS.has(e.name)) continue;
    fs.cpSync(path.join(src, e.name), path.join(dest, e.name), { recursive: true });
  }
}

// dir 配下の変更（ユーザー操作の書き込み・削除）をコミットして push する。
// リポジトリでなければ黙ってスキップ。変更も未 push コミットも無ければ何もしない。
//
// **本体の index / 作業ツリー / ブランチには一切触らない。** 専用の worktree を立て、その中
// だけでステージ・コミット・push する。
//
// 以前は本体に対して `git add -A -- <dir>` していた。プロジェクトルート（.kiro-project）は
// 成果物リポジトリの中にあり、独自の .git を持たないため、その rev-parse --show-toplevel は
// 成果物リポジトリ本体を指す。結果、viewer の操作 1 回で bus/ の実行記録が数百ファイル、
// 人が作業中のステージングへ流れ込んだ（人の index を乗っ取る）。worker が一時クローンで
// 作業して本体を汚さないのと同じ隔離を、viewer の git 操作にも与える。
//
// worktree はリモートの最新（FETCH_HEAD）から立てる。本体のローカルブランチが古くても
// push が non-fast-forward にならず、本体を進める必要もない（ローカルへの反映は既存の
// pull に任せる＝作業ツリーの更新を人の意思の下に置く）。
async function commitPush(dir, { message = 'kiro-projects-viewer: 操作を反映' } = {}) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch {
    return { skipped: true, notRepo: true };
  }
  // toplevel は git が realpath で返す（macOS の /var → /private/var 等）。dir 側も実体へ
  // 揃えてから相対化しないと、symlink を跨いだだけで「リポジトリ外」と誤判定する。
  let rel;
  try {
    rel = path.relative(fs.realpathSync(top), fs.realpathSync(dir));
  } catch {
    return { skipped: true, notRepo: true };
  }
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) {
    // プロジェクトがリポジトリ直下そのもの／外にある構成は、隔離コミットの対象にしない
    return { skipped: true, notRepo: true };
  }
  return enqueue(top, async () => {
    const up = await upstreamOf(top);
    if (!up) {
      // 押し出し先が無ければ隔離コミットは行き場を失う（worktree の detached commit は
      // どのブランチからも辿れず GC される）。本体を触らない方針は曲げず、明示的に返す。
      return { skipped: true, toplevel: top, noUpstream: true };
    }

    const wt = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-wt-'));
    fs.rmSync(wt, { recursive: true, force: true }); // worktree add は既存パスを嫌う
    let added = false;
    try {
      lastRemoteAt.set(top, Date.now());
      const fetched = await git(top, ['fetch', up.remote, up.branch], 120000);
      const base = fetched.code === 0 ? 'FETCH_HEAD' : 'HEAD'; // オフラインでも進める
      const wtAdd = await git(top, ['worktree', 'add', '--detach', wt, base], 60000);
      if (wtAdd.code !== 0) throw fail(wtAdd, 'git worktree add');
      added = true;

      syncInto(dir, path.join(wt, rel));

      const addRes = await git(wt, ['add', '-A', '--', rel]);
      if (addRes.code !== 0) throw fail(addRes, 'git add');
      const staged = await git(wt, ['diff', '--cached', '--quiet', '--', rel]);
      if (staged.code === 0) {
        return { skipped: false, toplevel: top, committed: false, pushed: false };
      }
      const commit = await git(wt, ['commit', '-m', message, '--', rel]);
      if (commit.code !== 0) throw fail(commit, 'git commit');

      // push 競合は「リモートを取り直して worktree を作り直す」ことで吸収する（force push
      // はしない）。本体を rebase しないので、人の作業ツリーは最後まで無傷のまま。
      let lastErr = null;
      for (let i = 0; i < PUSH_RETRIES; i++) {
        lastRemoteAt.set(top, Date.now());
        const push = await git(wt, ['push', up.remote, `HEAD:${up.branch}`], 120000);
        if (push.code === 0) {
          return { skipped: false, toplevel: top, committed: true, pushed: true };
        }
        lastErr = push;
        if (i === PUSH_RETRIES - 1) break;
        await sleep(2000 * 2 ** i);
        const re = await git(top, ['fetch', up.remote, up.branch], 120000);
        if (re.code !== 0) continue;
        const rebase = await git(wt, ['rebase', 'FETCH_HEAD'], 120000);
        if (rebase.code !== 0) {
          await git(wt, ['rebase', '--abort'], 30000);
          throw fail(rebase, 'git rebase（push 競合の取り込み）');
        }
      }
      throw fail(lastErr, 'git push');
    } finally {
      if (added) await git(top, ['worktree', 'remove', '--force', wt], 30000);
      fs.rmSync(wt, { recursive: true, force: true });
    }
  });
}

module.exports = { pull, commitPush, syncInto, RUNTIME_DIRS };
