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
  },
};
