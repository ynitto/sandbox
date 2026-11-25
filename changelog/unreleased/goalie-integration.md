### tools/goalie

- 目標管理と受け箱を goalie に統合し、旧パス `tools/agent-dots/` と `tools/copilot-app-jobs/` を廃止した。目標管理は `goalie.py`、受け箱は `goalie.py job` を使う。DAG 例と Teams ブリッジの提出先も更新した。
- 既存の目標は `.dot/` を `.goalie/` に、フックの `.github/hooks/agent-dots.json` を `.github/hooks/goalie.json` に手動で名前変更する。フックや自動実行のコマンドも新しいパスに更新する。進行中の `-dot` 依頼は切り替え前に完了させるか、`--name goalie` で出し直す。
- 既存の listen DAG は change-watch の consumer を `dots` から `goalie` に切り替える。オフセットは別管理のため、必要に応じて切り替え前に旧 consumer で未処理コメントを処理する。
