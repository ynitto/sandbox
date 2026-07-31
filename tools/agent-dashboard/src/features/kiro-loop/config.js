'use strict';

module.exports = {
  kiroLoop: {
    // capture-pane ポーリング間隔（renderer）。0 で停止。
    captureSec: 2,
    // 一覧に出す tmux セッション名の接頭辞（'kiro' は kiro-loop-… と send の既定
    // セッション 'kiro' の両方を拾う。tmux セッション内で起動されたデーモンのペインは
    // 名前によらず ~/.kiro/loop-state の状態ファイルから発見する）
    sessionPrefix: 'kiro',
  },
};
