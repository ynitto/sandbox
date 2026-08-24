'use strict';

// 共通ホームは `.agents`。プロジェクトローカル設定の読み取りだけ旧 `.agent` も候補に残す。
//
// **どのホームの `.agents` か**が肝心: 定常業務のエンジン（agent-loop / agentcore の
// CLI 群）は WSL 側で動くので、control.json や node-budget などノード横断の共有状態の
// 実体も WSL 側の `~/.agents` にある。dashboard（Windows）が os.homedir() だけで組むと
// `C:\Users\…\.agents` を読み書きしてしまい、エンジンと**別々のファイル**になる——
// 画面で保存した control.json がエンジンに永久に見えない。そこで Windows では
// WSL ホーム（UNC）を優先する。WSL が無い環境ではこのマシンのホームに戻る。

const fs = require('fs');
const os = require('os');
const path = require('path');

const AGENT_HOME = '.agents';
const AGENT_HOME_LEGACY = '.agent';

// 共有状態のルート候補（先頭が正典＝書き込み先）。
// Windows では WSL 既定ディストロのホーム（UNC・60 秒キャッシュ）を正典とし、
// このマシン（Windows 側）のホームも候補に並べる——旧配置で Windows 側に溜まった
// 状態や、Windows ネイティブで動くツールの状態も見えるようにする。
function sharedHomeRoots() {
  const local = os.homedir();
  if (process.platform !== 'win32') return [local];
  let wslHome = '';
  try {
    wslHome = require('./wsl').wslHomeDir();
  } catch {
    /* WSL が無い環境では Windows 側のホームだけ */
  }
  if (!wslHome || wslHome.toLowerCase() === local.toLowerCase()) return [local];
  return [wslHome, local];
}

// 共有状態のルート（`.agents` や `.kiro` を置くホーム）。
// Windows では WSL 既定ディストロのホーム（UNC・60 秒キャッシュ）を優先する。
function sharedHomeRoot() {
  return sharedHomeRoots()[0];
}

// dir が共有ホーム配下（<root>/.agents/…）のとき、他ホームの同じ場所を
// [{base: <root>/.agents, dir: 同じ相対位置}] で返す。ホーム外の dir（明示指定）は []。
function _sharedSiblings(dir) {
  const roots = sharedHomeRoots();
  if (roots.length < 2 || !dir) return [];
  const d = String(dir).toLowerCase();
  for (const root of roots) {
    const base = path.join(root, AGENT_HOME);
    const b = base.toLowerCase();
    if (d !== b && !d.startsWith(b + path.sep)) continue;
    const rel = String(dir).slice(base.length);
    return roots
      .filter((r) => r !== root)
      .map((r) => ({
        base: path.join(r, AGENT_HOME),
        dir: path.join(r, AGENT_HOME) + rel,
      }));
  }
  return [];
}

function _atomicWriteJson(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, target);
}

// 共有状態ファイルの読み取り先: dir が共有ホーム配下なら、両ホームの同じファイルの
// うち実在して最も新しいものを選ぶ（同時刻・どこにも無いは正典側）。ホーム外の
// dir（明示指定）は従来どおり単一。
function sharedStateReadPath(dir, file) {
  const primary = path.join(dir, file);
  let best = '';
  let bestM = -Infinity;
  for (const p of [primary, ..._sharedSiblings(dir).map((s) => path.join(s.dir, file))]) {
    let m;
    try {
      m = fs.statSync(p).mtimeMs;
    } catch {
      continue;
    }
    if (m > bestM) {
      bestM = m;
      best = p;
    }
  }
  return best || primary;
}

// 共有状態ファイルの原子書換: dir へ書き、`.agents` を既に持つ他ホームへも同じ内容を
// ミラーする（`.agents` の無いホームは汚さない）。ミラーの失敗は保存の成否に含めない
// ——正典へ書ければ保存は成立していて、ミラーは「もう一方のホームを見るツールにも
// 最新を見せる」ための補助でしかない。
function writeSharedStateJson(dir, file, obj) {
  _atomicWriteJson(path.join(dir, file), obj);
  for (const sibling of _sharedSiblings(dir)) {
    if (!fs.existsSync(sibling.base)) continue;
    try {
      _atomicWriteJson(path.join(sibling.dir, file), obj);
    } catch {
      /* ミラー失敗は握る */
    }
  }
}

// 共通ホームの実パス。
function agentHomeDir(base) {
  const root = base || sharedHomeRoot();
  return path.join(root, AGENT_HOME);
}

// 共通ホーム配下の状態ディレクトリ。
function agentHomeSubdir(...parts) {
  return path.join(sharedHomeRoot(), AGENT_HOME, ...parts);
}

// 設定ファイルの探索候補（読み取り専用なので新旧どちらも並べる。新しい方を先に見る）。
function agentDirCandidates(base) {
  const dirs = [path.join(base, AGENT_HOME)];
  if (!userHomeRoots().some((home) => path.resolve(home) === path.resolve(base))) {
    dirs.push(path.join(base, AGENT_HOME_LEGACY));
  }
  return dirs;
}

// 定常業務の走査に**必ず含めるユーザーホーム**。
//
// agent-loop の設定はプロジェクトの下だけでなく `~/.agents/agent-loop.yml` にも置かれる
// （その人の端末で回す定期処理）。ホームはどの登録簿（cowork.roots / host.yaml）にも
// 載らないので、明示的に足さないと画面から永久に見えない。
// Windows では WSL 側のホームも並べる——定常業務のエンジンは WSL で動くため、人が
// 「ホーム」と呼ぶ実体はそちらにもある。
function userHomeRoots() {
  const out = [os.homedir()];
  let wslHome = '';
  try {
    wslHome = require('./wsl').wslHomeDir();
  } catch {
    /* WSL が無い環境では Windows 側のホームだけ */
  }
  if (wslHome && !out.some((d) => d.toLowerCase() === wslHome.toLowerCase())) out.push(wslHome);
  return out;
}

module.exports = {
  AGENT_HOME,
  AGENT_HOME_LEGACY,
  agentHomeDir,
  agentHomeSubdir,
  agentDirCandidates,
  sharedHomeRoot,
  sharedHomeRoots,
  sharedStateReadPath,
  userHomeRoots,
  writeSharedStateJson,
};
