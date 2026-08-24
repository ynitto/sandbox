'use strict';

// 共通ホームは `.agents`。プロジェクトローカル設定の読み取りだけ旧 `.agent` も候補に残す。
//
// **どのホームの `.agents` か**が肝心: 定常業務のエンジン（agent-loop / agentcore の
// CLI 群）は WSL 側で動くので、control.json や node-budget などノード横断の共有状態の
// 実体も WSL 側の `~/.agents` にある。dashboard（Windows）が os.homedir() だけで組むと
// `C:\Users\…\.agents` を読み書きしてしまい、エンジンと**別々のファイル**になる——
// 画面で保存した control.json がエンジンに永久に見えない。そこで Windows では
// WSL ホーム（UNC）を優先する。WSL が無い環境ではこのマシンのホームに戻る。

const os = require('os');
const path = require('path');

const AGENT_HOME = '.agents';
const AGENT_HOME_LEGACY = '.agent';

// 共有状態のルート（`.agents` や `.kiro` を置くホーム）。
// Windows では WSL 既定ディストロのホーム（UNC・60 秒キャッシュ）を優先する。
function sharedHomeRoot() {
  if (process.platform === 'win32') {
    let wslHome = '';
    try {
      wslHome = require('./wsl').wslHomeDir();
    } catch {
      /* WSL が無い環境では Windows 側のホームを使う */
    }
    if (wslHome) return wslHome;
  }
  return os.homedir();
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
  userHomeRoots,
};
