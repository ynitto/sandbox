'use strict';

// kiro-projects / kiro-flow の状態を共有する git リポジトリとの同期層。
//   pull      … 設定間隔（既定 300 秒・下限 60 秒）で律速した取り込み。
//   commitPush … ユーザー操作（指示・投入・記入・削除）の都度反映。
// kiro-projects 本体の StateGit / kiro-flow の GitBus と同じ護りで、
// 同一クローンへコミットする他プロセス（本体の state_git・git-file-sync 等）と
// 共存できるようにする:
//   ・ステージは操作したディレクトリの pathspec だけ（add -A -- <dir>）。
//     他プロセスがステージした無関係な変更を自分のコミットに巻き込まない
//   ・push 競合は pull --rebase → 再 push の指数バックオフで吸収し、
//     force push は決してしない（他者のコミットを壊さない）
//   ・ロック起因の失敗（index.lock 等）はリトライし、30 秒以上古い残骸は自己回復
//   ・自プロセス内はリポジトリ単位の直列化キューで排他（pull と push を重ねない）

const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');

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

// pull 本体。rebase=true はローカルコミット（都度プッシュの書き込み）と共存する取り込み。
// rebase が進められない（コンフリクト）ときは abort して作業ツリーを壊さずエラーで返す
async function doPull(toplevel, rebase) {
  lastRemoteAt.set(toplevel, Date.now()); // 失敗しても間隔は空ける（リモートへの連打を防ぐ）
  const args = rebase ? ['pull', '--rebase'] : ['pull', '--ff-only'];
  const res = await git(toplevel, args, 120000);
  if (res.code !== 0) {
    if (rebase) await git(toplevel, ['rebase', '--abort'], 30000);
    throw fail(res, `git ${args.join(' ')}`);
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

// dir 配下の変更（ユーザー操作の書き込み・削除）をコミットして push する。
// リポジトリでなければ黙ってスキップ。変更も未 push コミットも無ければ何もしない。
async function commitPush(dir, { message = 'kiro-projects-viewer: 操作を反映' } = {}) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch {
    return { skipped: true, notRepo: true };
  }
  return enqueue(top, async () => {
    // ステージ・コミットとも pathspec を dir に限定し、他プロセスの変更を巻き込まない
    const addRes = await git(top, ['add', '-A', '--', dir]);
    if (addRes.code !== 0) throw fail(addRes, 'git add');
    const staged = await git(top, ['diff', '--cached', '--quiet', '--', dir]);
    let committed = false;
    if (staged.code !== 0) {
      const commit = await git(top, ['commit', '-m', message, '--', dir]);
      if (commit.code !== 0) throw fail(commit, 'git commit');
      committed = true;
    }
    // 押し出すもの（今回のコミット or 以前 push に失敗した分）が無ければリモートに触れない
    if (!committed && (await aheadCount(top)) === 0) {
      return { skipped: false, toplevel: top, committed: false, pushed: false };
    }
    // push 競合は pull --rebase → 再 push で吸収（force push はしない）。
    // push 先は追跡ブランチを明示（ローカルブランチ名や push.default に依存しない）
    const up = await upstreamOf(top);
    const pushArgs = up ? ['push', up.remote, `HEAD:${up.branch}`] : ['push'];
    let lastErr = null;
    for (let i = 0; i < PUSH_RETRIES; i++) {
      lastRemoteAt.set(top, Date.now());
      const push = await git(top, pushArgs, 120000);
      if (push.code === 0) {
        return { skipped: false, toplevel: top, committed, pushed: true };
      }
      lastErr = push;
      const pl = await git(top, ['pull', '--rebase'], 120000);
      if (pl.code !== 0) {
        await git(top, ['rebase', '--abort'], 30000);
        throw fail(pl, 'git pull --rebase（push 競合の取り込み）');
      }
      if (i < PUSH_RETRIES - 1) await sleep(2000 * 2 ** i);
    }
    throw fail(lastErr, 'git push');
  });
}

module.exports = { pull, commitPush };
