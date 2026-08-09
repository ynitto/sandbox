# Cowork feature

Cowork は agent-dashboard の独立した制御面です。

- 作業は `cowork.items` にフラットに並びます。`type: "loop"` は `agent-loop`、`type: "state-machine"` は `statemachine-use` で実行します。
- 各作業は `repo` で全体設定に登録済みのフォルダ（リポジトリ）を参照します。追加 UI では登録済みリポジトリから選択します。
- 実行エンジンが担当するプロジェクト配下の定常業務ループ設定 / `.statemachine/*/workflow.yaml` は自動発見します。ループ設定の探索先は `.agents/agent-loop.{yaml,yml,json}`（旧ホーム `.agent/` はそこにしか無いときのフォールバック）で、これは agent-loop 自身の探索と同じです。旧 `.agents/agent-loop.*` とフォルダ直下の `agent-loop.*` も**読むだけ**受けます（agent-loop 退役までの互換。両方あれば agent-loop 側が勝ちます）。発見結果は短時間キャッシュし、ポーリングごとに再走査しません（キャッシュの鍵は走査ルート＝エンジン担当 + `cowork.roots` なので、フォルダの登録・解除は即座に効きます）。
- **書き先は読取候補と分けます。** 画面で作った管理項目は、既にあるループ設定があればそこへ、無ければ `.agents/agent-loop.yml` を新規に作って書きます。旧 `.agents/agent-loop.*` は読めても新規の書き先にはしません——agent-loop が読まないファイルを増やすことになるためです。既存設定が `.json` のフォルダは画面から編集できません（YAML の外科的書換を JSON へ流し込むと壊れ、避けて `.yml` を作ると agent-loop の探索順で元の `.json` が無視されるため、どちらも取らずエラーにします）。
- 実行エンジンの管理外のフォルダは `cowork.roots` に登録すると走査対象になります。登録の入口は**⚙ 全体設定の「定常業務」**です。定常業務画面はプロジェクトを 1 つ選んでから開く画面なので、エンジンがまだ動いていない初期状態ではそこからは 1 件目を登録できません（全体設定はプロジェクト選択に依存しません）。
- `loopCommand` で定期実行のコマンドを切り替えます（**既定は `agent-loop`**）。設定は 1 つだけで、旧 `loopProvider`（種類）は読み取り互換のためだけに残しています。
- loop の単発実行は `<loopCommand> send <プロンプト名>` で行います（`run` サブコマンドは存在しません）。`send` はワークスペース（cwd）のループ設定から定期プロンプト名を解決し、稼働中の tmux セッションへ送信します。項目に `args` を明示した場合はそちらを優先します。
- `statemachine-use` は CLI ではなく**スキル**です。ステートマシンの実行は `<loopCommand> send "xxx ステートマシンを実行して"` でエージェントセッションへプロンプトを送ってスキルを発動します。
- ループ設定の prompts に「xxx ステートマシンを実行して」のような**対エントリ**（本文が `.statemachine/<name>` のフォルダ名か workflow.yaml の表示名に言及し「ステートマシン」を含む）がある場合、その loop 項目は対のステートマシン項目へ**統合して表示**します。統合項目は schedule / enabled を対エントリから引き継ぎ、実行は対プロンプト名の `send`、編集の書き戻しは schedule / enabled → ループ設定側・name / description → workflow.yaml 側に振り分けます。
- 状態表示のために新しい状態ファイルは作りません。既存ログ（`.agent-loop/logs` / 旧 `.agent-loop/logs` / `.statemachine-use/logs` / `logs`）から動的に推定します。プロセス探査（`pgrep` / `wmic`）はポーリングでは行わず、実行直後や手動更新時だけ行います。
- UI はメインの **Cowork タブ**に統一しています。左ペインには出しません。作業（手動登録または発見）が 1 件も無いときはタブ自体を非表示にします。
- 一覧は**選択中プロジェクトの作業だけ**を表示します（WSL UNC と POSIX パスは同一視）。「すべてのプロジェクトを表示」で全件に切り替えられます。
- 各作業の「履歴」から、**この画面からの実行記録**（`~/.agent-dashboard/cowork-history.jsonl` に追記・上限超は新しい方だけ残す）と、リポジトリの**実行ログ**（`.agent-loop/logs` / 旧 `.agent-loop/logs` / `.statemachine-use/logs` / `logs`）の一覧・末尾を確認できます。ログの読み出しはその作業のログ候補に実在するパスだけを許可します。
- agent-loop は WSL 側にしか無い想定のため、Windows 上の dashboard からの実行は**リポジトリが Windows ドライブ上でも常に `wsl.exe` 経由**でプロジェクトルート（`C:\...` は `/mnt/c/...` に変換）から行います。出力は UTF-8（失敗時は Shift_JIS）でデコードします。git 操作は WSL UNC のリポジトリのみ `wsl.exe` 経由です。
- Windows では実行を**新しいコンソールウィンドウ（WSL）で開始**します（既定。`cowork.runWindow: false` で従来の非表示実行に戻せます）。ウィンドウ実行は **ループ CLI を介しません** — tmux セッションに**エージェント CLI をインタラクティブ起動**し、dashboard が解決したプロンプトを直接送信して、そのまま **`tmux attach`** で実行の様子を見続けられます（`Ctrl+b d` で離脱）。
  - **どの CLI とモデルで起動するか**: 全体設定 →「実行制御」→「機能ごとのエージェントとモデル」の**定常業務**（agent-control 契約の `workloads.routine.agent_cli` / `model`）が最優先。空欄なら `defaults` → ⚙ AIアシスタント設定（`agent.cli` / `agent.model`）→ プロジェクト設定（`agent-project.yaml` / `agent-flow.yaml`）→ 既定 `kiro` の順に委ねます。起動 argv は CLI 定義（`agents/<name>.json` の `interactive`）が正典なので、`ollama` のように**モデル名を argv に載せる CLI**（`agent-ollama --tui --think on <model>`）も指定どおりに起動します。`cowork.chatCommand` は明示上書きで、既定は空です。
  - **送るプロンプト**: loop 項目（および対エントリを持つ統合項目）はループ設定に書かれた**プロンプト本文**。それ以外のステートマシン項目は「**statemachine-use スキルで〈ステートマシン名〉ステートマシンを実行して**」（入力があれば「。入力: …」を付加）。本文を解決できないときは、エージェント自身に設定ファイルを読ませる指示文で代替します。
  - **入力の補助**: `{{…}}` プレースホルダーやステートマシンの入力パラメータなど、ユーザー入力が必要な項目が埋まっていない場合に備え、「仮の値で進めず、先に必要な入力を質問してから実行する」補助文を自動で付け加えます。不足があればウィンドウ内のエージェント CLI が質問してくるので、そのまま対話で埋められます。
  - セッション名は `kiro-dash-<repoダイジェスト>`（リポジトリごとに再利用。`kiro` 接頭辞なので「端末」タブの一覧にも載ります）。入力受付の待ち方（`interactive.ready_pattern` / `ready_timeout_sec`）は CLI 定義から来ます。入力を受け付けたのを確認してから送信します。
  - ウィンドウは `cmd /s /c start … wsl.exe -e sh -lc ". '<一時スクリプト>'"` で開きます。GUI プロセス（Electron main）からコンソールアプリを直接 spawn しても対話可能なコンソールは割り当てられない（ウィンドウが出ない・`tmux attach` 不可）ため、`start` に新しいコンソールを作らせます。スクリプト本文は `%TEMP%\agent-dashboard\` の一時ファイルに書いて cmd の引用規則を回避します。
  - 明示 `args` を持つ手動項目は従来どおり `<loopCommand>` をウィンドウ内で実行します（レガシー経路）。
- `loopCommand` / `chatCommand` は**複数語のコマンド**（例 `python3 ~/sandbox/tools/agent-loop/agent-loop.py`）も指定できます。空白入りパスは `"…"` / `'…'` で囲みます。先頭の `~` は WSL 側の `$HOME` で展開されます（全体を 1 トークンとして引用すると `not found` になっていた問題を修正）。
