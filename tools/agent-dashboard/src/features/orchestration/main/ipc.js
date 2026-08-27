'use strict';

const budget = require('./budget');
const control = require('./control');
const agents = require('./agents');
const instructions = require('./instructions');
const sessionCommands = require('./sessionCommands');
const profiles = require('./profiles');
const tuning = require('./tuning');
const executionPolicy = require('./execution-policy');
const qualifications = require('./qualifications');
const recommendation = require('./recommendation');
const effectiveAgents = require('./effective-agents');
const herdFamily = require('./herd-family');

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
    const methodsCatalog = tuning.catalog(cfg);
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
      profiles: profiles.load(cfg),
      qualifications: qualifications.load(cfg),
      // 役割別の実効起動形（読み取り専用）。実行レベルで選んだ候補は、実行直前に
      // variants で用途ごとに振り替えられ、モデルまで変わることがある。その事実が
      // 画面のどこにも出ていなかったので「設定したのと違うものが動く」に見えていた。
      effectiveAgents: effectiveAgents.effectiveTable(cfg, profiles.load(cfg).tiers,
        { herdMembers: herdFamily.members(cfg) }),
      tuning: tuning.load(cfg),
      methodsCatalog: methodsCatalog.map((method) => ({
        ...method,
        catalog_source: `methods/${method.id}@${tuning.sourceHash(method)}`,
      })),
      tuningDir: tuning.resolveTuningDir(cfg),
      methodsDir: tuning.resolveMethodsDir(cfg),
    };
  });

  // 実行プロファイル自動選択（agent-profiles 契約）。
  // profilesSave: tiers / policy の宣言を保存（state は触らない）。
  // profilesEvaluate: dry-run（書かずに決定と根拠だけを返す）。
  // profilesApply: 決定を control.json（選択結果・差分があるときだけ）と
  //   profiles.json（state=記録）へ書く。
  handle('orchestration:profilesSave', (payload) => profiles.save(loadConfig(), payload || {}));
  handle('orchestration:profilesEvaluate', () => profiles.evaluate(loadConfig()));
  handle('orchestration:profilesApply', (options) => profiles.apply(loadConfig(), options || {}));
  handle('orchestration:executionPolicySave', (payload) =>
    executionPolicy.save(loadConfig(), payload || {})
  );

  // おすすめ構成（agent-recommendation）。読むだけの面と、適用の起動口。
  // **dashboard は推奨を生成しないし、適格性も書かない**——生成は eval の
  // recommend.py、適格性の writer は agent-audit だけ（設計 §4.2 の不変条件）。
  handle('orchestration:recommendation', (payload) => {
    const cfg = loadConfig();
    const loaded = recommendation.load(cfg);
    if (!loaded.exists) return { ...loaded, preflight: [], diff: [], cloudChoices: [] };
    const slotChoices = (payload && payload.slotChoices) || {};
    return {
      ...loaded,
      preflight: recommendation.preflight(cfg, loaded.document),
      cloudChoices: recommendation.cloudChoices(cfg),
      diff: recommendation.diff(cfg, loaded.document, slotChoices),
    };
  });
  handle('orchestration:recommendationApply', (payload) =>
    recommendation.apply(loadConfig(), payload || {}));

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

  // セッション開始コマンド（agent-session-commands 契約）の保存（revision +1）。
  handle('orchestration:sessionCommandsSave', (payload) =>
    sessionCommands.saveSessionCommands(loadConfig(), payload || {})
  );
  handle('orchestration:methodSet', (payload) => tuning.setMethod(loadConfig(), payload || {}));
  handle('orchestration:methodAdd', (payload) => tuning.addMethod(loadConfig(), payload || {}));
}

module.exports = { registerIpc };
