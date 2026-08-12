'use strict';

module.exports = {
  adhocFlow: {
    // アドホック run のバス（既定 ~/.agents/flow/bus）。プロジェクトのバスとは分ける——
    // アドホック実行は agent-project の状態（バックログ・納品）に一切触れないため、
    // 置き場も交わらないのが分かりやすい。
    busDir: '',
    // agent-flow の起動コマンド。空なら PATH の agent-flow を使う。
    // 例: python3 ~/repo/tools/agent-flow/agent-flow.py
    agentFlowCommand: '',
    // WSL ディストロ名。空なら既定ディストロ（Windows でのみ意味を持つ）
    distro: '',
    // 手法スナップショット（run 専用 AGENT_TUNING_DIR）の置き場。
    // 空なら ~/.agents/flow/tuning/<run-id>/
    tuningRoot: '',
    // 空なら ~/.agents/workflows。workflowDir はテスト・ポータブル環境用の上書き。
    // Git cwd の .agent-flow/workflows はこの値と別に自動探索する。
    workflowDir: '',
    // 実行に成功した cwd の履歴（新しい順、最大20件）。入力表記はそのまま保持する。
    cwdHistory: [],
    // 終端済みで未対応の確認が無い run の保持日数。
    retentionDays: 30,
    lastRetentionSweepAt: '',
    // 旧版互換。初回表示時に ~/.agents/workflows/*.json へ移行する。
    presets: [],
  },
};
