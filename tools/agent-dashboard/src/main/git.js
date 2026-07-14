'use strict';

// agent-project / agent-flow の状態を共有する git リポジトリとの同期層。
//   pull      … 設定間隔（既定 300 秒・下限 60 秒）で律速した取り込み。
//   commitPush … ユーザー操作（指示・投入・記入・削除）の都度反映。
// agent-project 本体の StateGit / agent-flow の GitBus と同じ護りで、
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

// 同期しない実行時データ（プロジェクトルート直下の名前）。agent-flow の bus は run ごとに
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

// 古いロック残骸の除去。worktree では .git がファイルで実体は .git/worktrees/<n>/ 配下に
// あるため、<top>/.git/<lock> の直書きでは永遠に見つからない（rebasing と同じ地雷）。
// rev-parse --git-path でロックごとの実体パスを解決する。
async function removeStaleLocks(toplevel) {
  let removed = 0;
  const now = Date.now();
  for (const name of STALE_LOCKS) {
    const res = await gitOnce(['-C', toplevel, 'rev-parse', '--git-path', name], 10000);
    const p = res.code === 0 && res.out
      ? (path.isAbsolute(res.out) ? res.out : path.join(toplevel, res.out))
      : path.join(toplevel, '.git', name);
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
    if ((await removeStaleLocks(toplevel)) === 0 && i < LOCK_RETRIES - 1) {
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
  // では agent-project 本体が watch 中 5 秒ごとに状態ファイル（project.json / journal.md /
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

// 指定パスだけを worktree 側へ反映する（存在しなければ削除として反映）。
// run の削除・inbox への投入など「操作が触ったパス」だけをコミットするための入り口。
// 全体コピー（syncInto）だと、操作と無関係な bus の揮発ファイル（meta / claims / events）まで
// その瞬間のスナップショットでコミットされ、本体の state 同期と同じファイルを取り合って
// 分岐を量産する（実運用では approve のコミットが bus を大量削除していた）。
function syncPathsInto(src, dest, paths) {
  for (const p of paths) {
    const from = path.join(src, p);
    const to = path.join(dest, p);
    fs.rmSync(to, { recursive: true, force: true });
    if (fs.existsSync(from)) {
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.cpSync(from, to, { recursive: true });
    }
  }
}

// dir 配下の変更（ユーザー操作の書き込み・削除）をコミットして push する。
// リポジトリでなければ黙ってスキップ。変更も未 push コミットも無ければ何もしない。
//
// **本体の index / 作業ツリー / ブランチには一切触らない。** 専用の worktree を立て、その中
// だけでステージ・コミット・push する。
//
// 以前は本体に対して `git add -A -- <dir>` していた。プロジェクトルート（.agent-project）は
// 成果物リポジトリの中にあり、独自の .git を持たないため、その rev-parse --show-toplevel は
// 成果物リポジトリ本体を指す。結果、viewer の操作 1 回で bus/ の実行記録が数百ファイル、
// 人が作業中のステージングへ流れ込んだ（人の index を乗っ取る）。worker が一時クローンで
// 作業して本体を汚さないのと同じ隔離を、viewer の git 操作にも与える。
//
// worktree はリモートの最新（FETCH_HEAD）から立てる。本体のローカルブランチが古くても
// push が non-fast-forward にならず、本体を進める必要もない（ローカルへの反映は既存の
// pull に任せる＝作業ツリーの更新を人の意思の下に置く）。
// paths を渡すと「操作が触ったパス（dir 相対）」だけを反映・ステージする。省略時は dir 全体
// （RUNTIME_DIRS を除く）を反映するが、その場合も実行記録をうっかり「削除」としてコミット
// しないよう、コピーしなかった RUNTIME_DIRS はベースの内容へ戻してからステージする。
async function commitPush(dir, { message = 'agent-dashboard: 操作を反映', paths = null } = {}) {
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

      let stagePaths;
      if (Array.isArray(paths) && paths.length) {
        syncPathsInto(dir, path.join(wt, rel), paths);
        stagePaths = paths.map((p) => path.posix.join(rel, p.split(path.sep).join('/')));
      } else {
        syncInto(dir, path.join(wt, rel));
        // syncInto がコピーしなかった実行記録（RUNTIME_DIRS）をベースの内容へ戻す。
        // 戻さないと add -A が「worktree に無い」＝一括削除としてステージし、リモートの
        // bus（本体が鏡写しした run の実行記録）を操作のたびに消し飛ばす。
        for (const name of RUNTIME_DIRS) {
          await git(wt, ['checkout', '-q', '--', path.posix.join(rel, name)]); // 無ければ失敗して無害
        }
        stagePaths = [rel];
      }

      const addRes = await git(wt, ['add', '-A', '--', ...stagePaths]);
      if (addRes.code !== 0) throw fail(addRes, 'git add');
      const staged = await git(wt, ['diff', '--cached', '--quiet', '--', ...stagePaths]);
      if (staged.code === 0) {
        return { skipped: false, toplevel: top, committed: false, pushed: false };
      }
      const commit = await git(wt, ['commit', '-m', message, '--', ...stagePaths]);
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

// ---------------------------------------------------------------------------
// 同期の健康状態と一発修復
// ---------------------------------------------------------------------------

// rebase が進行中か。worktree では .git が **ファイル** なので <top>/.git/rebase-merge を
// 直に見ても永遠に一致しない（agent-project の状態 worktree がまさにそれで、中断 rebase を
// 検知できず 🩺 が「解決しない」ボタンになっていた。Python 側 DirectStateGit._rebasing と
// 同じ地雷）。必ず rev-parse --git-path で実体パスを解決する。
async function rebasing(toplevel) {
  for (const d of ['rebase-merge', 'rebase-apply']) {
    const res = await git(toplevel, ['rev-parse', '--git-path', d], 10000);
    if (res.code !== 0 || !res.out) continue;
    const p = path.isAbsolute(res.out) ? res.out : path.join(toplevel, res.out);
    try {
      if (fs.statSync(p).isDirectory()) return true;
    } catch {
      /* 無ければ進行中でない */
    }
  }
  return false;
}

// 同期の健康状態（ローカル参照のみ・リモートへは触らない＝ポーリングに載せても無害）。
// summary は技術用語を避けた一文。level: ok | warn | error。
async function health(dir) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch {
    return { notRepo: true, level: 'ok', summary: 'git 同期なし（このフォルダだけで動いています）' };
  }
  const up = await upstreamOf(top);
  const dirtyRes = await git(top, ['status', '--porcelain'], 30000);
  const dirty = dirtyRes.code === 0 ? dirtyRes.out.split('\n').filter(Boolean).length : 0;
  const midRebase = await rebasing(top);
  let ahead = 0;
  let behind = 0;
  if (up) {
    const a = await git(top, ['rev-list', '--count', '@{u}..HEAD'], 10000);
    const b = await git(top, ['rev-list', '--count', 'HEAD..@{u}'], 10000);
    ahead = a.code === 0 ? parseInt(a.out, 10) || 0 : 0;
    behind = b.code === 0 ? parseInt(b.out, 10) || 0 : 0;
  }
  let level = 'ok';
  let summary = '同期は正常です';
  // rebase 中は HEAD が detached になり追跡ブランチも読めないため、必ず先に判定する
  if (midRebase) {
    level = 'error';
    summary = '前回の同期が途中で止まっています（🩺 同期を修復 で直せます）';
  } else if (!up) {
    level = 'warn';
    summary = '共有先（origin の追跡ブランチ）が未設定のため、この PC の中だけで動いています';
  } else if (ahead > 0 && behind > 0) {
    level = 'error';
    summary = `この PC と共有先の履歴が食い違っています（こちら ${ahead} 件・向こう ${behind} 件。🩺 同期を修復 で合流できます）`;
  } else if (behind > 0) {
    level = 'warn';
    summary = `共有先に未取得の更新が ${behind} 件あります（⇣ で取り込めます）`;
  } else if (ahead > 0) {
    level = 'warn';
    summary = `この PC に未送信の変更が ${ahead} 件あります（次の操作か 🩺 で送信されます）`;
  }
  return { notRepo: false, toplevel: top, upstream: up, ahead, behind, dirty, midRebase, level, summary };
}

// 一発修復: 詰まりの残骸を除去 → 取り込み → 送信、をまとめて行い、やったことを平易な文で返す。
// force push はしない・人の未コミット変更は消さない（安全側）。
async function heal(dir) {
  const top = await toplevelOf(dir); // 非 git はここで明示エラー（ボタンは非 git では出さない）
  return enqueue(top, async () => {
    const steps = [];
    if ((await removeStaleLocks(top)) > 0) steps.push('残っていたロックファイルを掃除しました');
    if (await rebasing(top)) {
      await git(top, ['rebase', '--abort'], 30000);
      steps.push('途中で止まっていた同期処理を巻き戻しました');
    }
    const up = await upstreamOf(top);
    if (!up) {
      return { steps, level: 'warn', summary: '共有先が未設定のため、掃除だけ行いました' };
    }
    lastRemoteAt.set(top, Date.now());
    const fetched = await git(top, ['fetch', up.remote, up.branch], 120000);
    if (fetched.code !== 0) {
      return {
        steps,
        level: 'error',
        summary: `共有先に接続できません（ネットワークか認証を確認してください）: ${(fetched.err || '').slice(-200)}`,
      };
    }
    steps.push('共有先の最新を確認しました');
    const a = await git(top, ['rev-list', '--count', `${up.remote}/${up.branch}..HEAD`], 10000);
    const b = await git(top, ['rev-list', '--count', `HEAD..${up.remote}/${up.branch}`], 10000);
    const ahead = a.code === 0 ? parseInt(a.out, 10) || 0 : 0;
    const behind = b.code === 0 ? parseInt(b.out, 10) || 0 : 0;
    if (behind > 0) {
      if (ahead === 0) {
        const res = await git(top, ['merge', '--ff-only', `${up.remote}/${up.branch}`], 120000);
        if (res.code !== 0) {
          return {
            steps,
            level: 'error',
            summary:
              '取り込みが編集中のファイルとぶつかりました。編集を保存・確定してからもう一度 🩺 を押してください',
          };
        }
        steps.push(`共有先の更新 ${behind} 件を取り込みました`);
      } else {
        // 分岐: この PC のコミット（viewer の指示・記入）は追加ファイル中心なので rebase で
        // ほぼ必ず通る。ただし誰かが書き込み中（作業ツリーが汚れている）なら走らせない
        // （--autostash は退避と書き込みが競合して状態ファイルへコンフリクトマーカーを
        //  書き込んだ前科がある）。通らなければ巻き戻して報告する（人の作業は壊さない）。
        const dirtyRes = await git(top, ['status', '--porcelain'], 30000);
        if (dirtyRes.code === 0 && dirtyRes.out.trim()) {
          return {
            steps,
            level: 'warn',
            summary:
              '書き込み中のファイルがあるため合流を見送りました。agent-project 本体が動いていれば' +
              '数分内に本体側が自動で合流させます（急ぐ場合は少し待って 🩺 を再度押してください）',
          };
        }
        const res = await git(top, ['rebase', `${up.remote}/${up.branch}`], 180000);
        if (res.code !== 0) {
          await git(top, ['rebase', '--abort'], 30000);
          return {
            steps,
            level: 'error',
            summary:
              '履歴の食い違いを自動では直せませんでした（同じファイルを両側で編集しています）。' +
              'agent-project 側が動いていれば数分待つと本体が合流させます',
          };
        }
        steps.push(`食い違っていた履歴を合流させました（こちら ${ahead} 件・向こう ${behind} 件）`);
      }
    }
    const a2 = await git(top, ['rev-list', '--count', `${up.remote}/${up.branch}..HEAD`], 10000);
    const ahead2 = a2.code === 0 ? parseInt(a2.out, 10) || 0 : 0;
    if (ahead2 > 0) {
      const push = await git(top, ['push', up.remote, `HEAD:${up.branch}`], 120000);
      if (push.code !== 0) {
        return {
          steps,
          level: 'warn',
          summary: `未送信の変更 ${ahead2} 件を送れませんでした（もう一度 🩺 を押すと再試行します）`,
        };
      }
      steps.push(`未送信の変更 ${ahead2} 件を送信しました`);
    }
    if (!steps.length) steps.push('問題は見つかりませんでした');
    return { steps, level: 'ok', summary: '同期は正常です' };
  });
}

// 検収サブ画面用: 作業ブランチの差分（ファイル指定可）。サイズ上限付き。
async function diffRange(repo, { base, ref, file, maxBytes = 200_000 } = {}) {
  const root = path.resolve(String(repo || ''));
  if (!root || !fs.existsSync(root)) throw new Error(`リポジトリが見つかりません: ${repo}`);
  const baseRef = String(base || 'main').trim() || 'main';
  const tip = String(ref || '').trim();
  if (!tip) throw new Error('比較先ブランチ／ref がありません');
  if (/[\s;|&`$]/.test(baseRef) || /[\s;|&`$]/.test(tip)) {
    throw new Error('不正な git ref です');
  }
  const args = ['-C', root, 'diff', '--no-color', `${baseRef}...${tip}`];
  const f = String(file || '').trim();
  if (f) {
    if (f.includes('..') || path.isAbsolute(f)) throw new Error('不正なファイルパスです');
    args.push('--', f);
  }
  const res = await gitOnce(args, 60000);
  if (res.code !== 0 && !res.out) {
    throw new Error(res.err || `git diff に失敗しました（exit ${res.code}）`);
  }
  let text = res.out || '';
  let truncated = false;
  const limit = Math.max(4_000, Number(maxBytes) || 200_000);
  if (text.length > limit) {
    text = text.slice(0, limit) + `\n…（差分が長いため ${limit} 文字で打ち切り）`;
    truncated = true;
  }
  return { text, truncated, repo: root, base: baseRef, ref: tip, file: f };
}

module.exports = { pull, commitPush, syncInto, syncPathsInto, health, heal, diffRange, RUNTIME_DIRS };
