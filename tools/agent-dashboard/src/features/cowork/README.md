# Cowork feature

Cowork は agent-dashboard の独立した制御面です。

- 作業は `cowork.items` にフラットに並びます。`type: "loop"` は `kiro-loop` / 将来の `agent-loop`、`type: "state-machine"` は `statemachine-use` で実行します。
- 各作業は `repo` で全体設定に登録済みのフォルダ（リポジトリ）を参照します。追加 UI では登録済みリポジトリから選択します。
- `projects.roots` 配下の `.kiro/kiro-loop.*` / `.statemachine/*/workflow.yaml` は自動発見します。発見結果は短時間キャッシュし、ポーリングごとに再走査しません。
- `loopProvider` / `loopCommand` で定期実行バックエンドを切り替えます。既定は `kiro-loop` ですが、呼び出しは provider 抽象越しなので `agent-loop` へ差し替えできます。
- loop の単発実行は `<loopCommand> send <プロンプト名>` で行います（`run` サブコマンドは存在しません）。`send` はワークスペース（cwd）の `.kiro/kiro-loop.*` から定期プロンプト名を解決し、稼働中の tmux セッションへ送信します。項目に `args` を明示した場合はそちらを優先します。
- 状態表示のために新しい状態ファイルは作りません。既存ログ（`.kiro-loop/logs` / `.agent-loop/logs` / `.statemachine-use/logs` / `logs`）から動的に推定します。プロセス探査（`pgrep` / `wmic`）はポーリングでは行わず、実行直後や手動更新時だけ行います。
- UI はメインの **Cowork タブ**に統一しています。左ペインには出しません。作業（手動登録または発見）が 1 件も無いときはタブ自体を非表示にします。
- Windows 上の dashboard から WSL 上のリポジトリを参照する場合、実行と git 操作は `wsl.exe` 経由で行い、出力は UTF-8（失敗時は Shift_JIS）でデコードします。
