# kiro-loop

kiro-cli を **tmux セッション**上で起動し、設定ファイルに定義したプロンプトを定期的に自動送信するツールです。

## 特徴

- **tmux ベース**: `kiro-cli chat` を tmux セッション内で実行し、`send-keys` / `capture-pane` で制御
- **出力の視認**: `attach` コマンドで tmux セッションにアタッチして kiro-cli の応答をリアルタイム確認
- **簡単な終了**: WSL ターミナルを閉じると SIGHUP を受信して自動終了（`quit` コマンドでも終了可）
- **複数ワークスペース対応**: 複数のプロジェクトディレクトリに対して個別に tmux セッションを管理
- **設定ファイル自動生成**: `add` コマンドでワークスペースを追加すると設定ファイルに自動保存
- **自動再起動**: kiro-cli が予期せず終了した場合に自動で再起動

## 依存

| 依存 | 必須/任意 | インストール |
|------|---------|-----------|
| `tmux` | **必須** | `sudo apt install tmux` |
| `PyYAML` | 任意（JSON 設定ファイルを使う場合は不要） | `pip install pyyaml` |

```bash
sudo apt install tmux
pip install pyyaml
```

## インストール

```bash
bash install.sh
```

または手動で：

```bash
cp kiro-loop.py ~/.local/bin/kiro-loop
chmod +x ~/.local/bin/kiro-loop
```

## 使い方

### パターン A: 設定ファイルなしで起動して対話的にワークスペースを追加

```bash
python /path/to/kiro-loop.py
```

起動すると `>` プロンプトが表示されます。`add` コマンドでワークスペースを追加すると
kiro-cli セッションが tmux 上で起動し、設定ファイル（`./kiro-loop.yaml`）に自動保存されます。

```
定期プロンプトが実行中です。'help' でコマンド一覧を表示します。
設定ファイル: /home/user/kiro-loop.yaml
> add project-a ~/projects/project-a
> add project-b ~/projects/project-b
> list
  名前                 状態     tmux セッション                  パス
  ------------------------------------------------------------------------------------
* project-a            [alive]  kiro-loop-project-a              /home/user/projects/project-a
  project-b            [alive]  kiro-loop-project-b              /home/user/projects/project-b
> attach project-a
tmux セッション 'kiro-loop-project-a' にアタッチします。
デタッチするには Ctrl+B D を押してください。
（別ウィンドウに切り替わり kiro-cli の出力を確認できます）
```

### パターン B: 設定ファイルを用意して起動

`kiro-loop.yaml.example` をコピーして編集します。

```bash
cp kiro-loop.yaml.example ~/kiro-loop.yaml
# workspaces / prompts を編集
python /path/to/kiro-loop.py --config ~/kiro-loop.yaml
```

起動時に設定ファイルの `workspaces` に定義したディレクトリで自動的に kiro-cli が起動します。

### 終了

- **ターミナルを閉じる** — SIGHUP を受信して自動クリーンアップ
- **`quit` コマンド** — `>` プロンプトで入力
- **Ctrl+C**

## コマンド一覧

起動後の `>` プロンプトで使えるコマンドです。

| コマンド | 説明 |
|---------|------|
| `add <name> <path>` | ワークスペースを追加して kiro-cli を tmux で起動（設定ファイルに自動保存） |
| `remove <name>` | ワークスペースを削除してセッションを停止（設定ファイルに自動保存） |
| `default <name>` | デフォルトワークスペースを変更（設定ファイルに自動保存） |
| `attach <name>` | tmux セッションにアタッチして kiro-cli の出力を確認（Ctrl+B D でデタッチ） |
| `list` | ワークスペースと tmux セッションの状態を一覧表示 |
| `status` | 実行状態を表示 |
| `save [path]` | 現在のワークスペース設定を保存（パス省略時は現在の設定ファイルに上書き） |
| `help` | コマンド一覧を表示 |
| `quit` / `exit` | 終了 |

## オプション

```
python kiro-loop.py [--config FILE] [--no-daemon] [--log-level LEVEL]

  --config FILE   設定ファイルのパス（省略時: カレントディレクトリ → HOME の順に自動検索）
  --no-daemon     対話モード: コマンドプロンプト（>）を表示して対話的に操作
                  デフォルトはデーモンモード（--daemon）で起動
  --log-level     DEBUG / INFO / WARNING / ERROR（デフォルト: INFO）
```

## タスクスケジューラ連携（Windows + WSL）

### 多重起動防止

kiro-loop は設定ファイルパスに基づく **PID ロックファイル**（`/tmp/kiro-loop-<hash>.pid`）で多重起動を防止します。

| 起動条件 | 挙動 |
|---------|------|
| 同じ設定ファイルで既に起動中 | 即座に終了（ログに PID を表示） |
| 異なる設定ファイル（別ディレクトリ） | 独立したインスタンスとして起動を許可 |
| 前回の kiro-loop が異常終了していた場合 | stale ロックを自動検出して起動 |

### デーモンモード（デフォルト動作）

デフォルトでデーモンモードで起動します（`--no-daemon` で対話モードに切り替え可能）。

- stdin を読まずに動作するため、タスクスケジューラから直接呼び出せる
- 定期プロンプト / セッション監視はバックグラウンドスレッドで継続動作
- `SIGTERM` / `SIGINT` / `SIGHUP` を受信するまでプロセスが生き続け、WSL のアイドルシャットダウンを回避
- 既に同じ設定で起動中なら即終了（多重起動なし）

### タスクスケジューラの設定例

WSL がアイドル終了した場合にも自動復旧するよう、**定期的に呼び出す**タスクを設定します。
kiro-loop が既に動いていれば即座に終了するため、頻繁に呼び出しても問題ありません。

**タスクスケジューラの設定:**

| 項目 | 設定値 |
|------|--------|
| トリガー | ログオン時 + 繰り返し間隔（例: 5 分ごと） |
| 操作 > プログラム | `wsl.exe` |
| 操作 > 引数 | `-- python3 /home/youruser/tools/kiro-loop/kiro-loop.py --config /home/youruser/kiro-loop.yaml` |
| 全般 > ユーザーがログオンしているかどうかにかかわらず実行する | チェック |
| 全般 > 最上位の特権で実行する | 不要 |
| 条件 > AC 電源の場合のみ実行する | オフ（ノート PC の場合） |

**コマンドラインからの手動テスト:**

```powershell
# PowerShell または cmd から
wsl -- python3 /home/youruser/tools/kiro-loop/kiro-loop.py --config /home/youruser/kiro-loop.yaml
```

**複数プロジェクトを別インスタンスで管理する場合:**

プロジェクトごとに設定ファイルを用意すると、タスクスケジューラから並行起動できます。

```powershell
# プロジェクト A（独立したインスタンス）
wsl -- python3 ~/tools/kiro-loop/kiro-loop.py --config ~/projects/app-a/kiro-loop.yaml

# プロジェクト B（独立したインスタンス）
wsl -- python3 ~/tools/kiro-loop/kiro-loop.py --config ~/projects/app-b/kiro-loop.yaml
```

## 設定ファイル形式 (YAML)

```yaml
# 監視するワークスペース（add コマンドで自動生成・更新される）
workspaces:
  - name: "project-a"
    path: "~/projects/project-a"
    default: true        # デフォルトワークスペース

  - name: "project-b"
    path: "~/projects/project-b"

# kiro-cli の起動オプション（全ワークスペース共通）
kiro_options:
  trust_all_tools: true  # ツール使用の確認をスキップ
  resume: false          # 直前のセッションを引き継ぐ
  # agent: my-agent
  # model: claude-sonnet

# タイムアウト（秒）
startup_timeout: 60      # kiro-cli 起動待ち
response_timeout: 300    # 1 プロンプトの応答待ち

# 定期プロンプト（省略可）
prompts:
  - name: "コードレビュー"
    prompt: "直近の変更のコードレビューをしてください。"
    interval_minutes: 30
    workspace: "project-a"   # 対象ワークスペース（省略時はデフォルト）
    enabled: true

  - name: "テスト実行"
    prompt: "テストを実行して結果を教えてください。"
    interval_minutes: 60
    workspace: "project-b"
    enabled: true
```

## tmux セッションの命名規則

ワークスペース名 `myproject` に対して `kiro-loop-myproject` という tmux セッションが作成されます。
英数字・`-`・`_` 以外の文字は `_` に置換されます。

```bash
# 全セッション確認
tmux list-sessions

# 手動でアタッチする場合
tmux attach-session -t kiro-loop-myproject
```

## 設定ファイルの検索順序

1. `--config` オプションで明示指定したパス
2. カレントディレクトリの `kiro-loop.yaml` / `kiro-loop.yml` / `kiro-loop.json`
3. HOME の `kiro-loop.yaml` / `kiro-loop.yml` / `kiro-loop.json`

設定ファイルが見つからない場合は `./kiro-loop.yaml` が保存先になります（`add` コマンド実行時に作成）。

## トラブルシューティング

### tmux が見つからない

```bash
sudo apt install tmux   # Ubuntu / WSL
```

### kiro-cli が起動しない

```bash
which kiro-cli   # PATH に kiro-cli があるか確認
kiro-cli chat    # 単体での動作確認
```

### プロンプト検出のタイムアウトが頻発する

kiro-cli のプロンプト表示形式が想定と異なる可能性があります。
`attach` コマンドでセッションにアタッチして実際の表示を確認してください。

```yaml
response_timeout: 600  # タイムアウトを延ばす（10 分）
```

### PyYAML がない

JSON 形式の設定ファイルを使うか、インストールしてください。

```bash
pip install pyyaml
```
