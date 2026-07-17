# Cowork feature

Cowork は agent-dashboard の独立した制御面です。

- 作業は `cowork.items` にフラットに並びます。`type: "loop"` は `kiro-loop` / 将来の `agent-loop`、`type: "state-machine"` は `statemachine-use` で実行します。
- 各作業は `repo` で全体設定に登録済みのフォルダ（リポジトリ）を参照します。追加 UI では登録済みリポジトリから選択します。
- `projects.roots` 配下の `.kiro/kiro-loop.*` / `.statemachine/*/workflow.yaml` は自動発見します。発見結果は短時間キャッシュし、ポーリングごとに再走査しません。
- `loopProvider` / `loopCommand` で定期実行バックエンドを切り替えます。既定は `kiro-loop` ですが、呼び出しは provider 抽象越しなので `agent-loop` へ差し替えできます。
- loop の単発実行は `<loopCommand> send <プロンプト名>` で行います（`run` サブコマンドは存在しません）。`send` はワークスペース（cwd）の `.kiro/kiro-loop.*` から定期プロンプト名を解決し、稼働中の tmux セッションへ送信します。項目に `args` を明示した場合はそちらを優先します。
- `statemachine-use` は CLI ではなく**スキル**です。ステートマシンの実行は `<loopCommand> send "xxx ステートマシンを実行して"` でエージェントセッションへプロンプトを送ってスキルを発動します。
- kiro-loop の prompts に「xxx ステートマシンを実行して」のような**対エントリ**（本文が `.statemachine/<name>` のフォルダ名か workflow.yaml の表示名に言及し「ステートマシン」を含む）がある場合、その loop 項目は対のステートマシン項目へ**統合して表示**します。統合項目は schedule / enabled を対エントリから引き継ぎ、実行は対プロンプト名の `send`、編集の書き戻しは schedule / enabled → kiro-loop 側・name / description → workflow.yaml 側に振り分けます。
- 状態表示のために新しい状態ファイルは作りません。既存ログ（`.kiro-loop/logs` / `.agent-loop/logs` / `.statemachine-use/logs` / `logs`）から動的に推定します。プロセス探査（`pgrep` / `wmic`）はポーリングでは行わず、実行直後や手動更新時だけ行います。
- UI はメインの **Cowork タブ**に統一しています。左ペインには出しません。作業（手動登録または発見）が 1 件も無いときはタブ自体を非表示にします。
- kiro-loop は WSL 側にしか無い想定のため、Windows 上の dashboard からの実行は**リポジトリが Windows ドライブ上でも常に `wsl.exe` 経由**でプロジェクトルート（`C:\...` は `/mnt/c/...` に変換）から行います。出力は UTF-8（失敗時は Shift_JIS）でデコードします。git 操作は WSL UNC のリポジトリのみ `wsl.exe` 経由です。
