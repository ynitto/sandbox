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
    // ウィンドウ実行時に tmux セッションで起動するインタラクティブ CLI。
    // kiro-loop は介さず、このセッションへプロンプトを直接送る。
    chatCommand: 'kiro-cli chat --trust-all-tools',
    // Flat work list. Each item references a repository already registered in
    // global settings: { id, type: 'loop'|'state-machine', name, repo, schedule, workflow }.
    items: [],
  },
};
