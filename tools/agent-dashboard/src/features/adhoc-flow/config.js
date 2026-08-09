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
    // 保存済みフロー定義（ビルダーの成果物）。実行時に投入契約（submit_request の plan）へ
    // 変換されるだけの宣言データで、独自の状態ファイルは作らない（config.json に同居）。
    presets: [],
  },
};
