'use strict';

// **読み取り専用の git 層**（実装計画 W2-1）。
//
// 常駐一本化（設計 §4.6）で、状態を共有するリポジトリへ書くのは常駐体（agent-project serve）
// だけになった。dashboard が pull / commit / push / rebase / ロック掃除をしていた経路は
// すべて削除済みで、ここに残るのは「見るだけ」の 3 つ:
//   health      … 同期の健康状態（ローカル参照のみ・リモートへは触らない）
//   diagnostics … 発見したプロジェクトの clone が有効か（誤設定を沈黙させない）
//   diffRange   … 検収サブ画面の差分表示（成果物リポジトリを読むだけ）
//
// この層に書き込みを足さないこと。同じクローンを常駐体が pull/push しているため、
// dashboard 側の書き込みは（過去に実際そうなったように）状態ファイルへコンフリクト
// マーカーを書き込む形で本体の状態を壊す。人の操作は契約ファイルの投函
// （commands/ inbox/ needs/ reviews/ assignments/）だけで届ける。

const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');

// ロック起因の git 失敗の再試行回数（1,2,4 秒バックオフ）。残骸の掃除は常駐体の仕事なので
// ここでは待つだけ——読み取りが数秒遅れても表示が古くなるだけで、実害は無い。
const LOCK_RETRIES = 4;

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
          GIT_EDITOR: 'true',
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

// ロック起因の失敗だけリトライする git 実行。それ以外の失敗はそのまま返す
async function git(toplevel, args, timeoutMs = 60000) {
  let res;
  for (let i = 0; i < LOCK_RETRIES; i++) {
    res = await gitOnce(['-C', toplevel, ...args], timeoutMs);
    if (res.code === 0 || !isLockError(res)) return res;
    if (i < LOCK_RETRIES - 1) await sleep(1000 * 2 ** i);
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

// 追跡ブランチ（origin/main 等）。
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

// ---------------------------------------------------------------------------
// 同期の健康状態（ローカル参照のみ）
// ---------------------------------------------------------------------------

// rebase が進行中か。worktree では .git が **ファイル** なので <top>/.git/rebase-merge を
// 直に見ても永遠に一致しない。必ず rev-parse --git-path で実体パスを解決する。
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

// summary は技術用語を避けた一文。level: ok | warn | error。
// リモートへは触らない（fetch もしない）＝ ahead/behind は前回 fetch 時点の追跡情報が根拠。
// 追跡情報を新しく保つのは常駐体の仕事で、その結果は engine/status.json の sync_health に載る。
async function health(dir) {
  let top;
  try {
    top = await toplevelOf(dir);
  } catch {
    return { notRepo: true, level: 'ok', summary: '共有なし（このフォルダだけで動いています）' };
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
  let summary = '共有先と揃っています';
  // rebase 中は HEAD が detached になり追跡ブランチも読めないため、必ず先に判定する
  if (midRebase) {
    level = 'error';
    summary = '前回の取り込みが途中で止まっています';
  } else if (!up) {
    level = 'warn';
    summary = '共有先が未設定のため、この PC の中だけで動いています';
  } else if (ahead > 0 && behind > 0) {
    level = 'error';
    summary = `この PC と共有先の内容が食い違っています（こちら ${ahead} 件・向こう ${behind} 件）`;
  } else if (behind > 0) {
    level = 'warn';
    summary = `共有先に未取得の更新が ${behind} 件あります`;
  } else if (ahead > 0) {
    level = 'warn';
    summary = `この PC に未送信の変更が ${ahead} 件あります`;
  }
  return { notRepo: false, toplevel: top, upstream: up, ahead, behind, dirty, midRebase, level, summary };
}

// セットアップ診断: 発見した各プロジェクトの clone が有効かを赤/緑で返し、誤設定を沈黙させない。
// roots は「発見済みプロジェクトの置き場」の配列（発見そのものは engine/status.json が担う）。
// 各 clone: { root, exists, isRepo, hasUpstream, level:'ok'|'warn'|'error', summary }。
async function diagnostics(role, roots) {
  const clones = [];
  for (const root of roots || []) {
    const r = String(root || '').trim();
    if (!r) continue;
    let exists = false;
    try {
      exists = fs.existsSync(r);
    } catch {
      exists = false;
    }
    let h = null;
    if (exists) {
      try {
        h = await health(r);
      } catch {
        h = null;
      }
    }
    const isRepo = !!(h && !h.notRepo);
    const hasUpstream = !!(h && h.upstream);
    const level = !exists ? 'error' : !isRepo ? 'warn' : !hasUpstream ? 'warn' : 'ok';
    const summary = !exists
      ? 'フォルダが見つかりません（⚙ 設定のディストロ・保存先を確認）'
      : !isRepo
      ? '共有用のフォルダではありません（実行側の設定を確認してください）'
      : !hasUpstream
      ? '共有先が未設定です（この PC の中だけで動いています）'
      : (h && h.summary) || '正常';
    clones.push({ root: r, exists, isRepo, hasUpstream, level, summary });
  }
  const worst = clones.some((c) => c.level === 'error')
    ? 'error'
    : clones.some((c) => c.level === 'warn')
    ? 'warn'
    : 'ok';
  return { role: role === 'viewer' ? 'viewer' : 'engineer', clones, level: worst };
}

// ---------------------------------------------------------------------------
// 検収サブ画面の差分（成果物リポジトリを読むだけ）
// ---------------------------------------------------------------------------

// repo は WSL 側の agent-project が記録した POSIX パス（/home/...）のことがある。
// Windows の dashboard では path.resolve が C:\home\... に化けて「リポジトリが見つかりません」
// になるため、WSL UNC（\\wsl.localhost\<distro>\...）へ変換してから解決する。
function bridgeRepoPath(repo, viewerRoot = '') {
  const { _isPosixAbs, toViewerPath } = require('../../features/agent-project/main/project');
  const raw = String(repo || '');
  if (process.platform !== 'win32' || !_isPosixAbs(raw)) return raw;
  // Windows dashboard から WSL を見る場合、delivery に残る repo は Linux パスだけで
  // ディストロ情報を持たない。既定 distro へ丸めると、実プロジェクトが別 distro にある環境で
  // \\wsl.localhost\<wrong>\... を開き、検収 diff が「リポジトリが見つからない」になる。
  // 画面で開いている project.dir（通常 effective_root_windows）から distro を引き継ぐ。
  const unc = String(viewerRoot || '').replace(/\//g, '\\');
  const m = unc.match(/^\\\\wsl(?:\$|\.localhost)\\([^\\]+)/i);
  if (m && m[1]) {
    return `\\\\wsl.localhost\\${m[1]}${raw.replace(/\//g, '\\')}`;
  }
  return toViewerPath(raw);
}

// 検収サブ画面用: 作業ブランチの差分（ファイル指定可）。サイズ上限付き。
//   fetch:true … 差分を取る前に git fetch origin して remote-tracking を最新化する
//                （コメント付き再実行で push し直した run の diff が古いまま、の対策）。
//                対象は成果物リポジトリ＝常駐体が同期する状態リポジトリではない。
//   branch     … 作業ブランチ名。fetch 後は origin/<branch> を最優先で比較先（tip）に使う
//                （記録済みの ref が古くても、今 push されている最新を見る）。
async function diffRange(repo, { base, ref, file, branch, fetch = false, maxBytes = 200_000, workingTree = false, viewerRoot = '' } = {}) {
  const root = path.resolve(bridgeRepoPath(repo, viewerRoot));
  if (!root || !fs.existsSync(root)) throw new Error(`リポジトリが見つかりません: ${repo}`);
  const bad = (s) => /[\s;|&`$]/.test(String(s || ''));
  const brName = String(branch || '').trim();
  if (brName && bad(brName)) throw new Error('不正なブランチ名です');

  // fetch はベストエフォート（オフライン等で失敗しても既存のローカル/追跡 ref で続行）。
  // branch が分かればそのブランチだけ引く（軽く速い）。
  if (fetch) {
    const fa = ['-C', root, 'fetch', '--quiet', 'origin'];
    if (brName && !brName.startsWith('origin/')) fa.push(brName);
    await gitOnce(fa, 60000);
  }

  const originOf = async (name) => {
    const n = String(name || '').trim();
    if (!n || bad(n)) return '';
    const rb = n.startsWith('origin/') ? n : `origin/${n}`;
    const chk = await gitOnce(['-C', root, 'rev-parse', '--verify', '--quiet', rb], 10000);
    return chk.code === 0 ? rb : '';
  };
  const exists = async (rev) => {
    if (!rev || bad(rev)) return false;
    return (await gitOnce(['-C', root, 'rev-parse', '--verify', '--quiet', rev], 10000)).code === 0;
  };

  // 比較先（tip）: fetch 後は origin/<branch> を最優先 → ローカル <branch> → 渡された ref。
  let tip = brName ? (await originOf(brName)) : '';
  if (!tip && brName && await exists(brName)) tip = brName;
  if (!tip) tip = String(ref || '').trim();
  if (bad(tip)) throw new Error('不正な git ref です');

  const useWorkingTree = workingTree || !tip;   // tip が取れなければ作業ツリー比較へ倒す
  const baseGiven = String(base || '').trim();
  if (baseGiven && bad(baseGiven)) throw new Error('不正な git ref です');

  // 差分の左辺（比較元）を決める。
  //   range   … 比較先が取れたとき。<base>...<tip>（検収の標準＝GitHub PR と同じ三点比較）。
  //             base も fetch 後は origin/<base> を優先する。
  //   working … 比較先が取れないとき。base（target）が分かるならその分岐点（merge-base）から
  //             作業ツリーまでを見せる。base が渡されないときだけ HEAD（純粋なローカル作業ツリー）。
  let leftSide;
  let usedBase = '';
  if (!useWorkingTree) {
    const b0 = baseGiven || 'main';
    const b = (await originOf(b0)) || b0;
    leftSide = `${b}...${tip}`;
    usedBase = b;
  } else {
    if (baseGiven) {
      const b = (await originOf(baseGiven)) || baseGiven;
      const mb = await gitOnce(['-C', root, 'merge-base', b, 'HEAD'], 10000);
      if (mb.code === 0 && String(mb.out || '').trim()) {
        leftSide = String(mb.out).trim();
        usedBase = b;
      }
    }
    if (!leftSide) leftSide = 'HEAD';
  }
  const args = ['-C', root, 'diff', '--no-color', leftSide];
  const f = String(file || '').trim();
  const internalPath = /(^|\/)\.agent-project\//;
  const excludes = [':(glob,exclude)**/.agent-project/**'];
  if (f) {
    if (f.includes('..') || path.isAbsolute(f)) throw new Error('不正なファイルパスです');
    if (internalPath.test(f.replace(/\\/g, '/'))) throw new Error('内部ファイルは検収対象にできません');
    args.push('--', f);
  } else {
    // 検収対象にはソース／ドキュメントだけを出し、実行状態ディレクトリは含めない。
    args.push('--', '.', ...excludes);
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
  let files = f ? [f] : [];
  if (!f) {
    const nameArgs = [
      '-C', root, 'diff', '--name-only', '-z', leftSide,
      '--', '.', ...excludes,
    ];
    const names = await gitOnce(nameArgs, 60000);
    if (names.code !== 0 && !names.out) {
      throw new Error(names.err || `変更ファイル一覧の取得に失敗しました（exit ${names.code}）`);
    }
    files = String(names.out || '').split('\0').filter(Boolean);
  }
  return {
    text,
    files,
    truncated,
    repo: root,
    base: usedBase,
    ref: tip,
    file: f,
    // working-tree でも target と比較できたら、単なるローカル差分と区別できるようにする。
    mode: useWorkingTree ? (usedBase ? 'working-tree-vs-target' : 'working-tree') : 'range',
  };
}

module.exports = { health, diffRange, bridgeRepoPath, diagnostics };
