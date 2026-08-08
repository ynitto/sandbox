# agent-loop

> **由来**: `tools/kiro-loop/` を置換せずクローンし改称した系統。改称後に `agent_loop/` パッケージへ
> モジュール分解（断片合成）。方針: [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md)。


kiro-cli を **tmux セッション**上で起動し、設定ファイルに定義したプロンプトを定期的に自動送信するツールです。

## 特徴

- **tmux ベース**: `kiro-cli chat` を tmux セッション内で実行し、`send-keys` / `capture-pane` で制御
- **出力の視認**: tmux 外から起動すると自動でセッションへアタッチ。`agent-loop ls` でも対象を確認可能
- **簡単な終了**: controller画面で `quit`、または Ctrl+C
- **ディレクトリ単位**: 起動したカレントディレクトリを対象に、プロンプトごとのペインを管理
- **設定ファイル自動生成**: `prompt-add` で定期プロンプトを追加すると `<project>/.agents/agent-loop.yml` に保存
- **自動再起動**: kiro-cli が予期せず終了した場合に自動で再起動
- **エージェント CLI の差し替え**: 設定 `agent_cli` で kiro-cli 以外（claude / codex 等）を `agents/<name>.json` 契約で駆動（待機判定・クリアコマンド・スキル起動記号も定義に従う）

## 依存

| 依存 | 必須/任意 | インストール |
|------|---------|-----------|
| `tmux` | **必須** | `sudo apt install tmux` |
| `PyYAML` | YAML設定と `prompt-add` / `prompt-remove` を使う場合に必要 | `pip install pyyaml` |

```bash
sudo apt install tmux
pip install pyyaml
```

## インストール

```bash
bash install.sh
```

## 使い方

対象プロジェクトへ移動して起動します。tmux 外では専用セッションを作成して自動アタッチします。

```bash
cd ~/projects/my-app
agent-loop
```

起動後の `>` プロンプトでは、定期プロンプトの追加・確認・削除と管理下ペインへの送信ができます。

```
定期プロンプトが実行中です。'help' でコマンド一覧を表示します。
> prompt-add 30 "コードをレビューしてください"
> prompt-list
> ls
```

### 終了

- **`quit` コマンド** — `>` プロンプトで入力
- **Ctrl+C**

## 起動後のコマンド

起動後の `>` プロンプトで使えるコマンドです。

| コマンド | 説明 |
|---------|------|
| `status` | 実行状態を表示 |
| `ls` | 管理下のプロンプトセッションを一覧表示 |
| `send <target> <text>` | pane ID・tmuxセッション名・プロンプト名を指定して送信 |
| `prompt-add [name] <interval> <prompt>` | 定期プロンプトを追加して保存 |
| `prompt-list` | 定期プロンプト設定を表示 |
| `prompt-remove <index>` | 定期プロンプトを削除して保存 |
| `help` | コマンド一覧を表示 |
| `quit` / `exit` | 終了 |

## CLI

```
agent-loop [--log-level LEVEL] [--split-direction horizontal|vertical] [--no-auto-attach]
agent-loop ls
agent-loop send [-s SESSION] [-d DIR] [--wait] [--priority high|normal|low] PROMPT
agent-loop pause | resume | cancel TARGET | drain | reload
agent-loop doctor [--json] [--fix]
agent-loop update
agent-loop msg --to AGENT [OPTIONS] [BODY]
agent-loop agents
agent-loop --version
```

- `agent-loop --version` は zipapp 内 `build-info.json` の commit、source 実行時は `git describe` / `dev` を表示します。
- `agent-loop update` は zipapp インストールのみ対象です（source / pip / symlink は理由付きで非 0）。稼働 daemon がある場合は update lock により拒否されます。成功後も実行中 daemon は自動再起動しません。
- 同じworkspaceのdaemon稼働中は、`send`を永続キュー（`~/.agents/send-requests/`）へ受付します。daemon不在時は従来どおりstandalone sessionへ直接送信します。
- `send --wait`はrequest ID単位の完了状態を待ち、別requestのbusy/ready遷移を完了扱いしません。
- `pause` / `resume` は local pause（`resume` は agent-control / budget の pause を迂回しません）。
- `cancel` は managed な entry / pane だけを停止・slot 解放します。
- `drain` は新規受付を止め、実行中完了後に daemon を終了します。
- `reload` は設定を検証してから次 tick で一括交換します（失敗時は旧設定を維持）。
- `doctor` は YAML / slot / send-request 等を診断します。

`--no-auto-attach` はtmux外で専用セッションへ自動接続しない場合に使います。多重起動は
`~/.agents/loop-state/`（旧 `~/.agent/` は移行時のみ）にある生存プロセスのcwdで判定します。

### environment handoff（opt-in）

```yaml
environment_handoff:
  prompt: false
  skill_home: null
  token_env_names: []
```

- ペイン起動時に `HOME` と `AGENT_HOME`（および agent 定義の `env`）を tmux 起動環境へ明示します。
- `prompt: true` のとき、root プロンプト先頭へ `[ENV]...[/ENV]` を付けます（Ralph child には付けません）。
- `token_env_names` は `[A-Z_][A-Z0-9_]*` のみ受理し、値は `SET|UNSET` だけを渡します。

## 設定ファイル形式 (YAML)

```yaml
# ~/.agents/agent-loop.yaml: 共通設定
# <project>/.agents/agent-loop.yml: このプロジェクトの定期プロンプト

# kiro-cli の起動オプション（agent_cli 未指定時のみ）
kiro_options:
  trust_all_tools: true  # ツール使用の確認をスキップ
  resume: false          # 直前のセッションを引き継ぐ
  # agent: my-agent
  # model: claude-sonnet

# エージェント CLI の差し替え（省略時は kiro-cli）。
# agents/<name>.json の interactive 定義から起動 argv と待機判定を解決する。
# agent_cli: claude
# agent_cli_options:
#   model: claude-sonnet-5
#   readonly: false
#   extra_args: []

# タイムアウト（秒）
startup_timeout: 60      # kiro-cli 起動待ち

# 定期プロンプト（省略可）
prompts:
  - name: "コードレビュー"
    prompt: "直近の変更のコードレビューをしてください。"
    interval_minutes: 30
    enabled: true

  - name: "テスト実行"
    prompt: "テストを実行して結果を教えてください。"
    interval_minutes: 60
    enabled: true

  # slash: 本文の前にスラッシュコマンドを送る（下記）
  - name: "ログ要約"
    slash: summarize-logs
    prompt: "昨日のログを要約して"
    interval_minutes: 60
    enabled: true

  # event_hook: 送信タイミング・内容を Python スクリプトで制御する
  - name: "GitLab Issue ワーカー"
    event_hook: ~/sandbox/tools/agent-loop/hooks/gitlab-issue-hook.py
    event_hook_fallback: true   # 更新が無くてもランダムに 1 件送る
    interval_minutes: 5
    enabled: true
```

### slash（本文の前にスラッシュコマンドを送る）

スキル呼び出しやモード切替のような**スラッシュコマンド**を、本文とは別に宣言できます。
本文へ `/name` を書き込む必要がなくなり、コマンドだけ差し替える・外すのが容易になります。

```yaml
prompts:
  - name: "定期点検"
    slash: ["healthcheck", "report --lang ja"]   # 文字列でも配列でも可
    prompt: "結果を 3 行で"
    interval_minutes: 240
```

- 各要素は `/<name> [引数]` という**独立した 1 送信**になります（本文へ連結しません。
  対話 CLI はスラッシュコマンドを「1 入力 = 1 コマンド」で解釈するため）。
- 送信順は `fresh_context` の `/clear` → `slash`（宣言順）→ `prompt` 本文。
- 先頭の `/` は書きません（付いていても剥がして送ります）。名前は `[a-z0-9][a-z0-9._-]*`。
  規約外の要素は**その要素だけ**捨てて警告します（タイポで定期駆動が止まらないように）。
- `prompt` を省いて `slash` だけのエントリも有効です（コマンドだけ定期送信）。
- スラッシュコマンドを解する対話 CLI なら何にでも使えます（特定の CLI 専用ではありません）。

詳細な仕様と移植手順は
[`docs/designs/agent-loop-slash-property-design.md`](../../docs/designs/agent-loop-slash-property-design.md)。

### event_hook（フックによる送信制御）

`event_hook` にフックスクリプトのパスを指定すると、スケジュール発火のたびに
フックの `check()` が呼ばれます。

```python
def check() -> str | None:
    ...  # str を返す→その内容を送信 / None を返す→今回はスキップ
```

- `event_hook` を使う場合 `prompt` は省略できます（フックが内容を決めるため）。
- `event_hook_fallback: true`（既定 `false`）にすると、フックに環境変数
  `AGENT_LOOP_EVENT_HOOK_FALLBACK=1` が渡されます。フック側はこれを見て
  「**発火すべき更新が無くても、フィルター条件に合致する対象をランダムに 1 件
  選んで送る**」フォールバックを実装できます。フォールバックは `check()` の
  呼び出しごと（イベント検知のタイミング）に毎回評価されます。

同梱フック例（`hooks/`）:

| フック | 動作 |
|---|---|
| `gitlab-issue-hook.py` | 新規/更新 Issue を検知して送信。更新が無くフォールバック有効ならランダムな Issue を送る。 |
| `gitlab-mr-hook.py` | 新規/更新 MR を検知して送信。更新が無くフォールバック有効ならランダムな MR を送る。 |

いずれも `gitlab-idd` スキルの `scripts/gl.py` を利用します。`GITLAB_TOKEN` を
設定し、必要に応じて環境変数（`AGENT_LOOP_GL_PY`, `AGENT_LOOP_GL_CWD`,
`AGENT_LOOP_ISSUE_LABELS` など）でパスやフィルター条件を上書きしてください。

## tmux セッションの命名規則

起動ディレクトリ、instance ID、用途から `agent-loop-<label>-<digest>-<instance>` 形式の
tmux セッションが作成されます。実際の名前は `agent-loop ls` で確認してください。

```bash
# 全セッション確認
tmux list-sessions

# 手動でアタッチする場合
tmux attach-session -t SESSION
```

## 設定ファイルの場所と優先順位

- 共通設定: `~/.agents/agent-loop.yaml` / `.yml` / `.json`（移行前は `~/.agent/`）
- プロジェクト設定: `<cwd>/.agents/agent-loop.yaml` / `.yml` / `.json`（移行前は `.agent/`）
- 共通設定が無い場合の互換入力: `<cwd>/.vscode/settings.json` の `agentExecutor.periodicPrompts`

プロジェクト設定の `prompts` があれば共通設定・VS Code由来の予定より優先します。
`prompt-add` / `prompt-remove` は既存の YAML、無ければ `<cwd>/.agents/agent-loop.yml` へ保存します。

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

### kiro-cli の起動待ちタイムアウトが頻発する

kiro-cli のプロンプト表示形式が想定と異なる可能性があります。
`agent-loop ls` でセッション名を確認し、`tmux attach-session -t SESSION` で実際の表示を確認してください。

```yaml
startup_timeout: 600  # 起動待ちを延ばす（10 分）
```

### PyYAML がない

JSON 形式の設定ファイルを使うか、インストールしてください。

```bash
pip install pyyaml
```
