# ワークフロー

既存の `adhoc-flow` feature を、agent-flow の実行とカスタムフロー管理を行う「ワークフロー」画面として使う。

## 実行

- Git 管理された cwd、依頼、フローを選ぶ。
- Windows の cwd は実行時だけ WSL の `/mnt/<drive>/...` に変換する。履歴は入力表記のまま最大20件保持する。
- フローは自動、`agent-flow patterns --json` の標準パターン、`~/.agents/workflows/*.json` のカスタムから選ぶ。
- inbox の `workspace` に cwd のリポジトリと現在の branch/HEAD を固定する。成果は `af/<run-id>` branch に保存される。

## 設定

- カスタムフローは1フロー1JSONファイル。
- ノードは `kind`、依存、位置、tier を持つ。
- 実行時に tier の利用可能な候補を `{agent_cli, model}` へ固定し、下位tierへは降格しない。
- 機能（kind）・オプション（continuation）ごとの実行可能レベルは orchestration の
  `flow-tiers.js` カタログが宣言する。範囲外の固定 tier は plan 生成で弾き、auto tier は
  実行方針が不適格な段を選びうる機能だけ今の段を適格範囲へ丸めて固定する（それ以外は
  従来どおり実行時の方針を継承する）。設計:
  `docs/plans/2026-08-12-agent-flow-tier-eligibility-strategy-design.md`。
- 旧 `adhocFlow.presets` は初回表示時にファイルへ移行する。

## バックログ連携

実行前レビューと再実行で同じフロー一覧を選べる。選択は `backlog/<task-id>.flow.json` に固定し、agent-project が `--pattern` または `--plan-file` として agent-flow へ渡す。sidecar が無い場合は自動。

テスト: `node test/adhoc-flow.test.js`
