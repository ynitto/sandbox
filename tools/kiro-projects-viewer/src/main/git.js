'use strict';

// 選択中プロジェクトのリポジトリを git pull で最新化する層。
// ビュアーは数秒間隔（既定 5 秒）でポーリングするため、そのたびに fetch する
// とリモートサーバに負荷をかける。リポジトリ（toplevel）単位で最終試行時刻を
// 覚え、設定間隔（下限 MIN_INTERVAL_SEC）内の自動 pull はスキップする。
// 手動（force）は間隔を無視するが、同一リポジトリへの同時実行はさせない。

const { execFile } = require('child_process');

// 自動 pull の下限間隔。設定でこれより短くしてもリモートへは詰めない
const MIN_INTERVAL_SEC = 60;

const lastPullAt = new Map(); // toplevel -> 最終試行 epoch ms（失敗も含む＝連打防止）
const inflight = new Map(); // toplevel -> 実行中の Promise

function git(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    execFile(
      'git',
      args,
      {
        timeout: timeoutMs,
        // 資格情報プロンプトで固まらせない（認証が要る場合は即失敗させる）
        env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      },
      (err, stdout, stderr) => {
        if (err) reject(new Error(String(stderr || err.message).trim().slice(-400)));
        else resolve(String(stdout).trim());
      }
    );
  });
}

async function toplevelOf(dir) {
  return git(['-C', dir, 'rev-parse', '--show-toplevel'], 10000);
}

// dir を含む git リポジトリを pull する。
//   force=false（ポーリングからの自動）… intervalSec 内はスキップ。
//     git リポジトリでない場合も黙ってスキップ（{skipped, notRepo}）。
//   force=true（手動ボタン）… 間隔を無視して実行。リポジトリでなければエラー。
// --ff-only なので作業ツリーを壊すマージは作らない（進められなければエラーで返る）。
async function pull(dir, { intervalSec = 300, force = false } = {}) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch (err) {
    if (!force) return { skipped: true, notRepo: true };
    throw new Error(`git リポジトリではありません: ${dir}（${err.message}）`);
  }
  if (inflight.has(top)) return inflight.get(top);

  const min = Math.max(MIN_INTERVAL_SEC, Number(intervalSec) || 0);
  const elapsed = (Date.now() - (lastPullAt.get(top) || 0)) / 1000;
  if (!force && elapsed < min) {
    return { skipped: true, toplevel: top, nextInSec: Math.ceil(min - elapsed) };
  }

  const p = (async () => {
    // 失敗しても試行時刻は更新する（失敗のたびにリモートへ連打しない）
    lastPullAt.set(top, Date.now());
    const output = await git(['-C', top, 'pull', '--ff-only'], 120000);
    return { skipped: false, toplevel: top, output: output.slice(-400) };
  })().finally(() => inflight.delete(top));
  inflight.set(top, p);
  return p;
}

module.exports = { pull };
