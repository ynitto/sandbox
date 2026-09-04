'use strict';

// 他の制御面への依存を閉じ込めるアダプタ。documents の本体（store / rules / sidecar /
// prompts）はこのモジュール以外から agent-project / cowork / loopProvider を参照しない。
// テストは module.exports の関数を差し替えて起動系を切り離せる（documents.js は呼ぶたびに
// `launcher.launchWindow(...)` とプロパティ経由で参照する）。
//
// 2 種類の起動:
//   launchWindow … 文書フォルダを cwd にした**書き込み可**の対話ウィンドウ。定常業務の
//                  アドホック起動と同じ部品（CLI/モデル解決・共通指示の前置・セッション開始
//                  コマンド）で起こす。interactive を持たない CLI は agent-loop の単発実行へ落とす
//   advise       … 読み取り専用のヘッドレス助言（Dashboard AI と同じ解決・予算・記帳）

const DOCUMENT_WORKLOAD = 'documents';
const SESSION_PREFIX = 'agent-doc';

// エージェント（WSL 側）から見たパス。
//
// 文書と文書ルールのフォルダは **Windows 側**に置く（store.js の homeRoot）が、
// 作成・続き・検証・助言のどれもエージェントは **WSL の中**で走る。
// `C:\Users\me\.agents\documents\提案書` のまま渡すと `cd` も刺さらず、依頼文に混ぜれば
// エージェントは存在しないパスを探しに行く。**WSL へ渡る値はすべてここを通す**
// ——起動の cwd（runChatWindow / runCommandWindow が内部で同じ変換をする）だけでなく、
// 依頼文に書く作業フォルダ（prompts の setDir）も同じ表記で揃える。
// win32 以外は素通し（ホームも実行も同じ Linux 側なので変換するものが無い）。
function agentPath(p) {
  const s = String(p || '');
  if (process.platform !== 'win32' || !s) return s;
  return require('../../../base/main/wsl').toWslPath(s);
}

// require は関数の中で行う（読み込み時に他の制御面を引き込まない）。
function agentModule() {
  return require('../../agent-project/main/agent');
}

function coworkModule() {
  return require('../../cowork/main/cowork');
}

// この端末で文書作成に使うエージェント（表示にも起動にも同じ解決を通す）。
function resolveDocumentAgent(config, cwd) {
  return agentModule().resolveAgent(config, cwd, { workload: DOCUMENT_WORKLOAD });
}

function describeAgent(config, cwd) {
  const r = resolveDocumentAgent(config, cwd);
  return { cli: r.cli, model: r.model, source: r.source, interactive: !!(r.spec && r.spec.interactive) };
}

function launchWindow(config, { cwd, prompt, title, sessionKey, message }) {
  const cowork = coworkModule();
  const agent = agentModule();
  const { runChatWindow } = require('../../cowork/main/loopProvider');
  const selected = resolveDocumentAgent(config, cwd);
  const fullPrompt = cowork.withGlobalInstructions(config, prompt);
  if (cowork.needsHeadlessHarness(selected.spec)) {
    return cowork.runHeadlessRoutine(config, {
      cwd, prompt: fullPrompt, acceptance: [], selected, title, record: () => {},
    });
  }
  const launch = agent.interactiveLaunchSpec(config, cwd, { workload: DOCUMENT_WORKLOAD, resolved: selected });
  const res = runChatWindow({
    chatCommand: launch.chatCommand,
    prompt: fullPrompt,
    cwd,
    sessionCommands: cowork.planSessionCommands(config, cwd, {
      agentCli: launch.cli, skillCommandPrefix: launch.skillCommandPrefix,
    }),
    readyPattern: launch.readyPattern,
    readyTimeoutSec: launch.readyTimeoutSec,
    sessionKey,
    sessionPrefix: SESSION_PREFIX,
    title,
    message: message || `別ウィンドウで ${launch.cli} を起動しました`,
  });
  return { ...res, cli: launch.cli, model: launch.model };
}

// ヘッドレスの助言。対話ウィンドウ（必ず wsl.exe 経由で開く）と揃えて、win32 では
// **常に WSL 側**で走らせる——置き場が Windows パスになった分、cwd が WSL UNC かどうかで
// 経路を決める runCommand の既定では Windows ネイティブ起動へ倒れ、WSL にしか入っていない
// エージェント CLI が「見つからない」になる。cwd の WSL 表記への変換は runAgent が行う。
async function advise(config, cwd, purpose, prompt) {
  const agent = agentModule();
  const resolved = agent.resolveDashboardAgent(config, cwd, { purpose });
  const raw = await agent.runDashboardAgent(config, resolved, purpose,
    () => agent.runAgent(resolved, prompt, cwd, { wsl: process.platform === 'win32' }));
  const text = agent.stripFence(raw);
  if (!String(text || '').trim()) throw new Error('エージェントの応答が空でした');
  return { text, cli: resolved.cli, model: resolved.model, source: resolved.source };
}

module.exports = {
  DOCUMENT_WORKLOAD, SESSION_PREFIX, agentPath, resolveDocumentAgent, describeAgent, launchWindow, advise,
};
