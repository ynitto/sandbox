'use strict';

module.exports = {
  cowork: {
    refreshSec: 10,
    loopProvider: 'kiro-loop',
    loopCommand: 'kiro-loop',
    nextLoopProvider: 'agent-loop',
    stateMachineCommand: 'statemachine-use',
    // Windows では実行を新しいウィンドウ（WSL tmux）で開始し、進行を見られるようにする。
    // false で従来の非表示実行（spawnSync）に戻す。
    runWindow: true,
    // ウィンドウ実行時に tmux セッションで起動するインタラクティブ CLI の**明示上書き**。
    // 既定は空文字＝「上書きしない」——起動する CLI は全体設定（実行制御の
    // workloads.routine.agent_cli / model）→ ⚙ アシスタント設定 → プロジェクト設定 の順で
    // 解決する（cowork.js の coworkChatLaunch）。
    //
    // ここに 'kiro-cli chat --trust-all-tools' を**既定値として**置いていたのが、
    // 「定常業務だけが全体設定を無視して常に kiro-cli で起動する」不具合の正体だった。
    // saveConfig は既定値も config.json へ書き戻すので、誰も触っていなくても必ず
    // 明示上書きが載っている状態になり、CLI 解決（S9-3）へ一度も到達しなかった。
    chatCommand: '',
    // Flat work list. Each item references a repository already registered in
    // global settings: { id, type: 'loop'|'state-machine', name, repo, schedule, workflow }.
    items: [],
    // 定常業務ワークスペースのフォルダ一覧（S2）。agent-project の管理外でも、kiro-loop 設定や
    // .statemachine/ を持つフォルダをこの画面から扱えるようにする。
    //
    // **なぜ agent-project の host.yaml ではなく dashboard 設定か**: 定常業務のエンジン
    // （kiro-loop / statemachine-use）は agent-project の常駐体・状態リポジトリ・バックログと
    // 無関係に動き、起動・tmux セッション管理・履歴記録はすべてこの cowork feature が担う。
    // W2-4 で廃止したのは「agent-project プロジェクト一覧の二重管理」で、その原則は
    // 「宣言は実行側が持つ」——定常業務の実行側は dashboard 自身なので、宣言もここに置く。
    // host.yaml に載せると、常駐体が管理しないものを常駐体の宣言ファイルに書くねじれになる。
    roots: [],
  },
};
