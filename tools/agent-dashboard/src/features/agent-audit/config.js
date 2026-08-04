'use strict';

module.exports = {
  agentAudit: {
    // agent-audit CLI の起動コマンド。空なら PATH の agent-audit を探す。
    // 例: python3 ~/repo/tools/agent-audit/agent-audit.py
    command: '',
    // WSL ディストロ名。空なら既定ディストロ（Windows でのみ意味を持つ）
    distro: '',
    // --config で渡す設定ファイル（WSL 内のパス）。空なら agent-audit 自身の探索順に任せる
    configPath: '',
    // 収集データの保存先 --audit-dir（WSL 内のパス）。空なら ~/.agents/audit
    auditDir: '',
    // 定期収集の間隔（分）。0 で無効
    collectIntervalMin: 0,
  },
};
