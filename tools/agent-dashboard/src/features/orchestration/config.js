'use strict';

// オーケストレーション制御面の設定既定。
// - budgetDir … ノード予算（node-budget v2 契約）の config.json + ledger/ の場所。
//   空 = 環境変数 AGENT_BUDGET_DIR → ~/.agent/budget（schemas/node-budget.schema.json が正典）。
// - controlDir … エージェント制御（agent-control 契約）の control.json + status/ の場所。
//   空 = 環境変数 AGENT_CONTROL_DIR → ~/.agent/control（schemas/agent-control.schema.json が正典）。
// - instructionsDir … グローバル指示（agent-instructions 契約）の instructions.json の場所。
//   空 = 環境変数 AGENT_INSTRUCTIONS_DIR → ~/.agent/instructions（schemas/agent-instructions.schema.json が正典）。
// - refreshSec … オーケストレーションタブの自動更新間隔（秒）。
//
// いずれもノード横断（マシン単位）の設定であり、プロジェクト選択には依存しない。

module.exports = {
  orchestration: {
    budgetDir: '',
    controlDir: '',
    instructionsDir: '',
    refreshSec: 15,
  },
};
