'use strict';

const budget = require('./budget');
const control = require('./control');
const agents = require('./agents');
const instructions = require('./instructions');
const sessionCommands = require('./sessionCommands');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;

  // 1 ポーリングでオーケストレーション面をまとめて返す:
  // 予算 usage v2（実測＋推定の内訳つき）・control 現在値・status/ 一覧（fresh 判定つき）・
  // エージェント CLI ドロップイン棚卸し・グローバル指示（現在値＋描画プレビュー）・
  // セッション開始コマンド（現在値。プレビューはエンジンを選ぶ必要があるため renderer 側で組む）。
  handle('orchestration:overview', () => {
    const cfg = loadConfig();
    const controlDir = control.resolveControlDir(cfg);
    const instructionsDir = instructions.resolveInstructionsDir(cfg);
    const gi = instructions.loadInstructions(instructionsDir);
    const sessionDir = sessionCommands.resolveSessionDir(cfg);
    return {
      sessionCommands: sessionCommands.loadSessionCommands(sessionDir),
      sessionDir,
      budget: budget.usage(cfg),
      control: control.loadControl(controlDir),
      status: control.readStatus(controlDir),
      agents: agents.list(cfg),
      instructions: gi,
      instructionsPreview: instructions.renderBlock(gi),
      instructionsDir,
      budgetDir: budget.resolveBudgetDir(cfg),
      controlDir,
    };
  });

  // 予算: 上限・期間・allocation（weight/min/max/on_exhausted/soft_ratio）
  handle('orchestration:budgetSave', (payload) => budget.save(loadConfig(), payload || {}));
  // アロケータの手動実行（auto では refreshSec ごとに自動）
  handle('orchestration:rebalance', () => budget.rebalance(loadConfig()));
  // レート較正（台帳の実測行から中央値を求め rates.per_cli へ書き戻す）
  handle('orchestration:calibrate', () => budget.calibrateRates(loadConfig()));

  // 制御: overrides / degraded / delegation の保存（revision +1）
  handle('orchestration:controlSave', (payload) => control.saveControl(loadConfig(), payload || {}));
  // lifecycle の近道（{workload, action: run|pause|stop}）
  handle('orchestration:lifecycle', (payload) => control.setLifecycle(loadConfig(), payload || {}));

  // ドロップイン定義の作成・編集・削除
  handle('orchestration:agentSave', (payload) => agents.save(loadConfig(), payload || {}));
  handle('orchestration:agentDelete', (payload) => agents.remove(loadConfig(), payload || {}));

  // グローバル指示（agent-instructions 契約）の保存（revision +1）とスキル候補の棚卸し。
  handle('orchestration:instructionsSave', (payload) =>
    instructions.saveInstructions(loadConfig(), payload || {})
  );
  handle('orchestration:skillsInventory', () => instructions.skillsInventory(loadConfig()));

  // セッション開始コマンド（agent-session-commands 契約）の保存（revision +1）と、
  // エンジンを指定した実行計画のプレビュー（プレースホルダ展開・when 判定・有界化まで済んだもの）。
  handle('orchestration:sessionCommandsSave', (payload) =>
    sessionCommands.saveSessionCommands(loadConfig(), payload || {})
  );
  handle('orchestration:sessionCommandsPreview', (payload) => {
    const p = payload || {};
    const data = p.data || sessionCommands.loadSessionCommands(
      sessionCommands.resolveSessionDir(loadConfig())
    );
    return sessionCommands.plan(data, p.context || {});
  });
}

module.exports = { registerIpc };
