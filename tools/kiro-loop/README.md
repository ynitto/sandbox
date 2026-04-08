# kiro-loop

kiro-cli をインタラクティブモードで起動し、設定ファイルに定義したプロンプトを定期的に自動送信するツールです。

## 特徴

- **インタラクティブモード**: `kiro-cli chat` を PTY 経由で制御するため、非インタラクティブモードの不安定さを回避
- **簡単な終了**: WSL ターミナルを閉じると SIGHUP を受信して自動終了（`quit` コマンドでも終了可）
- **複数ワークスペース対応**: 複数のプロジェクトディレクトリに対して個別にセッションを管理
- **設定ファイル自動生成**: `add` コマンドでワークスペースを追加すると設定ファイルに自動保存
- **自動再起動**: kiro-cli が予期せず終了した場合に自動で再起動

## 依存ライブラリ

| ライブラリ | 必須/任意 | インストール |
|-----------|---------|-----------|
| `pexpect` | **必須** | `pip install pexpect` |
| `PyYAML`  | 任意（JSON 設定ファイルを使う場合は不要） | `pip install pyyaml` |

```bash
pip install pexpect pyyaml
```

## インストール

```bash
# 任意のディレクトリにスクリプトを配置
cp kiro-loop.py ~/.local/bin/kiro-loop
chmod +x ~/.local/bin/kiro-loop

# または PATH が通った場所に symlink
ln -s /path/to/kiro-loop.py ~/.local/bin/kiro-loop
```

## 使い方

### パターン A: 設定ファイルなしで起動して対話的にワークスペースを追加

```bash
# どこからでも起動できる（設定ファイルは不要）
python /path/to/kiro-loop.py
```

起動すると `>` プロンプトが表示されます。`add` コマンドでワークスペースを追加すると
kiro-cli セッションが起動し、設定ファイル（`./kiro-loop.yaml`）に自動保存されます。

```
定期プロンプトが実行中です。'help' でコマンド一覧を表示します。
設定ファイル: /home/user/kiro-loop.yaml
> add project-a ~/projects/project-a
> add project-b ~/projects/project-b
> list
  名前                 状態     パス
  ----------------------------------------------------------
* project-a            [alive]  /home/user/projects/project-a
  project-b            [alive]  /home/user/projects/project-b
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
| `add <name> <path>` | ワークスペースを追加して kiro-cli を起動（設定ファイルに自動保存） |
| `remove <name>` | ワークスペースを削除してセッションを停止（設定ファイルに自動保存） |
| `default <name>` | デフォルトワークスペースを変更（設定ファイルに自動保存） |
| `list` | ワークスペースと kiro-cli セッションの状態を一覧表示 |
| `status` | 実行状態を表示 |
| `save [path]` | 現在のワークスペース設定を保存（パス省略時は現在の設定ファイルに上書き） |
| `help` | コマンド一覧を表示 |
| `quit` / `exit` | 終了 |

## オプション

```
python kiro-loop.py [--config FILE] [--log-level LEVEL]

  --config FILE   設定ファイルのパス（省略時: カレントディレクトリ → HOME の順に自動検索）
  --log-level     DEBUG / INFO / WARNING / ERROR（デフォルト: INFO）
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

# kiro-cli の出力をターミナルに表示するか（stderr に出力）
echo_output: true

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

## 設定ファイルの検索順序

1. `--config` オプションで明示指定したパス
2. カレントディレクトリの `kiro-loop.yaml` / `kiro-loop.yml` / `kiro-loop.json`
3. HOME の `kiro-loop.yaml` / `kiro-loop.yml` / `kiro-loop.json`

設定ファイルが見つからない場合は `./kiro-loop.yaml` が保存先になります（`add` コマンド実行時に作成）。

## トラブルシューティング

### kiro-cli が起動しない

```bash
which kiro-cli   # PATH に kiro-cli があるか確認
kiro-cli chat    # 単体での動作確認
```

### プロンプトのタイムアウトが頻発する

```yaml
response_timeout: 600  # 10 分
```

### 起動タイムアウトが発生する

```bash
python kiro-loop.py --log-level DEBUG
```

### PyYAML がない

JSON 形式の設定ファイルを使うか、インストールしてください。

```bash
pip install pyyaml
```

