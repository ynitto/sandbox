'use strict';

module.exports = {
  amigos: {
    refreshSec: 15,
    // ノード予算（node-budget 契約）の設定・台帳の場所。
    // 空 = $AGENT_BUDGET_DIR → ~/.agent/budget（schemas/node-budget.schema.json が正典）。
    budgetDir: '',
    // 監視する agent-amigos バス。ローカルバス（<dir>/missions/<mid>/）と
    // GitBus のクローン作業領域（<dir>/mission__<mid>/）のどちらの形も受ける。
    // 空のときは ~/.agent/amigos/bus/* （GitBus 既定 workdir）を自動発見する。
    busDirs: [],
    // 常駐デーモンのホーム（.agent/agent-amigos.yaml を持つディレクトリ）の明示指定。
    // 通常は実行エンジンが担当するプロジェクト配下の自動発見で足りる。ホームにはタスク依頼（post）と
    // 手動引き受け（claim）を .agent/agent-amigos/commands/ 経由で投函できる。
    homeDirs: [],
    // 上記の自動発見でプロジェクト配下を掘る深さ。
    scanDepth: 2,
  },
};
