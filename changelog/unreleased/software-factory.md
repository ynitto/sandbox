### tools/software-factory

- GitHub Issue と goalie の受け箱を照合し、ドラフト PR の作成とレビュー報告を行う factory-controller を追加した。設定の雛形を生成する `init`、照合する `poll` とその別名 `run-once`、状態を表示する `status` を備える。
- Copilot アプリ向けの実装・レビュー依頼の雛形と、5 分ごとにコントローラーを動かす Dagu の DAG 例を追加した。
- 状態遷移、冪等性、タイムアウト、CI、設定検証、投入保留を確認する単体テストを追加し、Ubuntu と Windows の CI に組み込んだ。
