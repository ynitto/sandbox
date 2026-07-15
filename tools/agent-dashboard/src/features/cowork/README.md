# Cowork feature

Cowork は agent-dashboard の独立した制御面です。

- 作業は `cowork.items` にフラットに並びます。`type: "loop"` は `kiro-loop` / 将来の `agent-loop`、`type: "state-machine"` は `statemachine-use` で実行します。
- 各作業は `repo` で全体設定に登録済みのフォルダ（リポジトリ）を参照します。追加 UI では登録済みリポジトリから選択します。
- `loopProvider` / `loopCommand` で定期実行バックエンドを切り替えます。既定は `kiro-loop` ですが、呼び出しは provider 抽象越しなので `agent-loop` へ差し替えできます。
- 状態表示のために新しい状態ファイルは作りません。既存ログ（`.kiro-loop/logs` / `.agent-loop/logs` / `.statemachine-use/logs` / `logs`）とプロセス一覧から動的に推定します。
- Windows 上の dashboard から WSL 上のリポジトリを参照する場合、実行と git 操作は `wsl.exe` 経由で行います。
