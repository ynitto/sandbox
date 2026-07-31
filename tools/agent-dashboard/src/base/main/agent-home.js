'use strict';

// エージェント共通ホームの解決。`.agent` から `.agents` へ改名した
// （複数のエージェントが相乗りする持ち物であることを名前で示す）。
//
// 旧ホームが残っている環境では、新ホームがまだ無い間だけ旧ホームを使う。両方を書き先に
// すると実行制御や予算の状態が分裂し、「どちらか片方だけ正しい」状況が生まれる。
// 読み取り専用の探索（設定ファイルの探索順など）は両方を候補に並べてよい——そちらは
// 書き込みを伴わないので分裂しない。

const fs = require('fs');
const os = require('os');
const path = require('path');

const AGENT_HOME = '.agents';
const AGENT_HOME_LEGACY = '.agent';

// 共通ホームの実パス。既定は <base>/.agents で、旧 <base>/.agent しか無ければそちら。
function agentHomeDir(base) {
  const root = base || os.homedir();
  const next = path.join(root, AGENT_HOME);
  const legacy = path.join(root, AGENT_HOME_LEGACY);
  if (!fs.existsSync(next) && fs.existsSync(legacy)) return legacy;
  return next;
}

// 共通ホーム配下の状態ディレクトリ。**判定はサブディレクトリ単位で行う。**
// ホーム単位で見ると、`.agents/skills` だけ先に作られた環境（スキル導入が先行した）で
// 「新ホームは在る」と判断され、まだ移していない `.agent/control` を見失う。
// 項目ごとに実在する方へ寄せれば、移行が部分的に進んだ状態でも状態は 1 か所に定まる。
function agentHomeSubdir(...parts) {
  const home = os.homedir();
  const next = path.join(home, AGENT_HOME, ...parts);
  const legacy = path.join(home, AGENT_HOME_LEGACY, ...parts);
  if (!fs.existsSync(next) && fs.existsSync(legacy)) return legacy;
  return next;
}

// 設定ファイルの探索候補（読み取り専用なので新旧どちらも並べる。新しい方を先に見る）。
function agentDirCandidates(base) {
  return [path.join(base, AGENT_HOME), path.join(base, AGENT_HOME_LEGACY)];
}

module.exports = {
  AGENT_HOME,
  AGENT_HOME_LEGACY,
  agentHomeDir,
  agentHomeSubdir,
  agentDirCandidates,
};
