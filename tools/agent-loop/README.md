# agent-loop

> 2026-08-09 に定期駆動ループを agent-loop へ一本化。経緯と移行方針は
> [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md) を参照。


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

YAMLから呼ぶ同梱スクリプトは実行ファイルと同じprefixの `hooks/`、CLI lifecycle用assetは
`agent-hooks/` へ配置されます。installerは各CLIを起動せず、global/project設定も変更しません。

### 旧設定の移行

旧ツールの設定はファイル名と置き場を変更し、`event_hook` は `hooks`、
`event_hook_config` は `hook_config` へ改名する。移行先が既にある場合は上書きせず、内容を統合する。

```bash
mkdir -p ~/.agents
cp ~/.kiro/kiro-loop.yaml ~/.agents/agent-loop.yaml

# ワークスペースごとの設定も同様
mkdir -p .agents
cp .kiro/kiro-loop.yml .agents/agent-loop.yml
```

移行後は `agent-loop doctor` で設定を検査し、`agent-loop` を起動する。旧ファイルはロールバック用に残してよいが、agent-loop は読まない。

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
agent-loop send [-s SESSION] [-d DIR] [--wait] [--priority high|normal|low]
                [--model MODEL] [--sandbox] [--force]
                [--ralph --max-iterations N] PROMPT
agent-loop run [--agent-cli NAME] [--model MODEL] [--acceptance TEXT ...] [-d DIR] PROMPT
agent-loop statemachine --workflow PATH [--agent-cli NAME] [--model MODEL]
                        [--param KEY=VALUE ...] [--input TEXT] [-d DIR]
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
- `--ralph` / `--sandbox` / `--force` / `--model` は同じ workspace の daemon が必須です。
- `--ralph --max-iterations N` は同一 pane で N 回送信し、最終回に要約指示を付けます（`--force` 併用不可）。
- `--sandbox` は git worktree を `~/.agents/sandboxes/` に作り、clean なら完了後に削除します。
- `--force` が迂回するのは visual ready と preflight だけです（lifecycle / slot / 追跡中 pane は迂回しません）。
- `run` はプロンプト 1 件をその場で 1 回実行します（daemon 不要）。`send` が「常駐セッションへ
  送る」のに対し、こちらは「今ここで実行して結果を返す」口です。ツールループ非内蔵の CLI
  （`headless_autonomy: single-shot`）には限定ツール契約でツール実行を供給し、`--acceptance`
  が無ければ結果は「検証なし」になります。終了時に `RESULT {json}` を 1 行出力します。
  機械が照合するのは受入条件のバッククォート内にある**パスの形をした表記**だけです
  （区切り `/` か拡張子を持つもの。`agent-audit` のようなコマンド名は照合対象外）。
  ツール契約の制御応答（次の一手の JSON）は、定義が `json_variant` を申告していれば
  その起動形へ振り替えます（編集は元の CLI のまま）。編集用 CLI に制御を兼ねさせると、
  材料が揃った時点でモデルが本文を書き始め、その周が捨てられます。
  tmux で様子を見せたいときは、このコマンドを tmux ウィンドウの中で起動してください
  （対話 CLI かどうかとは無関係——tmux は送る手段・見る手段）。
- `statemachine` は statemachine-use のワークフローを aider 等の **headless CLI** で完走させる
  ハーネスです（限定ツールループ: `read_files` / `write_files` / `run` / `final`。パス・
  実行ファイル検証と JSONL ログつき）。CLI とモデルは `agents/<name>.json` 契約で解決し、
  `--model` 省略時は定義の `default_model` を使います。状態遷移は statemachine-use の
  `next_state.py` が確定します。終了時に `RESULT {json}` を 1 行出力します
  （dashboard はこの行を実行結果の契約として読みます）。`--workflow` は作業ディレクトリ
  内のパスに限ります（dashboard からは cwd 相対で渡されます）。
- `pause` / `resume` は local pause（`resume` は agent-control / budget の pause を迂回しません）。
- `cancel` は managed な entry / pane だけを停止・slot 解放します（external pane は拒否）。
- `drain` は新規受付を止め、実行中完了後に daemon を終了します。
- `reload` は設定を検証してから次 tick で一括交換します（失敗時は旧設定を維持）。
- `doctor` は YAML / slot / send-request 等を診断します。

`--no-auto-attach` はtmux外で専用セッションへ自動接続しない場合に使います。多重起動は
`~/.agents/loop-state/` にある生存プロセスのcwdで判定します。

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

### 対話CLIのターン完了検知

`agents/<name>.json` が `interactive.turn_completion` を宣言する Kiro classic / Claude Code /
Codex / Copilot / OpenCode では、agent-loopが起動したmanaged paneにだけCLI固有hookを注入します。
native eventはinstance・pane・dispatch generation・random tokenを検証してからSlotMonitorへ渡し、
画面監視と同じcallback経路で一度だけ完了させます。asset欠落やCLIの仕様差では従来の画面監視へ
自動fallbackします。headless、external pane、手動起動したCLI、Cursor、Kiro v3には注入しません。

- Kiro classic: private `KIRO_HOME` へagents/prompts/skills/steeringとMCP設定だけをsnapshotし、
  選択中custom agentを複製してstop hookを追加します。sessions/logs/cache/authはコピーしません。
- Claude/Copilot: `--plugin-dir`を加算し、user/projectのinstructions・skills・MCP・plugin探索を維持します。
- Codex: `CODEX_HOME`を変えずone-off `notify`を多重化し、既存notifyも後段で実行します。
- OpenCode: pluginだけのprivate `OPENCODE_CONFIG_DIR`を加算し、既存config mergeを維持します。

これはagent-loop内部の完了検知で、YAMLの `hooks`（外部イベント取得スクリプト）とは別機能です。

### agent-tuning（汎用注入）

`$AGENT_TUNING_DIR`（既定 `~/.agents/tuning/`）の `tuning.json` で、プロンプト注入と
ペイン起動時の PATH・環境変数を宣言できます。エントリの `tuning_profile` で切り替え、
外向き成果物には `external-facing`（注入なし）を指定します。設定不在・破損・無効は no-op です。

同じ契約の `methods` / `trials` は、role と資源条件（実行プロファイルの段・agent CLI の
相対コストを含む）に合う追補指示を各プロンプトへ適用します。組み込みカタログは
`$AGENT_METHODS_DIR`（既定 `~/.agents/methods/`）にあり、次の CLI で管理できます。

```bash
agent-loop methods list [--json]
agent-loop methods enable test-first
agent-loop methods disable test-first
agent-loop methods add my-check --role session --text "完了前に証拠を確認する" --when-json '{"tiers":["small"]}'
```

`when.tiers` に書く実行レベルは、dashboardと共通の`basic`（短い一手順）/ `small` / `medium` /
`large`です。同じレベルの候補は同程度の複雑さを扱える前提で、料金区分とは分けて設定します。
実行レベルが宣言されていないノードでは値が決まらないので、`tiers`条件の手法は当たりません
（そのノードでも効かせたいなら `max_relative_cost: 0`（ローカル）や `agent_cli` で絞ります）。

`enable` はカタログを tuning.json へ複製して source hash を固定します。カタログの更新を
稼働へ反映するには、明示的にもう一度 `enable` します。

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

# 設定内の文字列から参照できる値
mapping:
  workspace:
    main: /path/to/workspace
  message:
    review: 直近の変更をレビューしてください

# 定期プロンプト（省略可）
prompts:
  - name: "コードレビュー"
    prompt: "{{lookup message review}}"
    cwd: "{{lookup workspace main}}"
    tuning_profile: default
    interval_minutes: 30
    enabled: true

  - name: "テスト実行"
    prompt: "テストを実行して結果を教えてください。"
    tuning_profile: external-facing  # 外向き成果物では文体注入を外す
    interval_minutes: 60
    enabled: true

  # slash: 本文の前にスラッシュコマンドを送る（下記）
  - name: "ログ要約"
    slash: summarize-logs
    prompt: "昨日のログを要約して"
    interval_minutes: 60
    enabled: true

  # hooks: 送信タイミング・内容を Python スクリプトで制御する
  - name: "GitLab Issue ワーカー"
    hooks: gitlab-issue-hook
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

詳細な仕様は
[`docs/designs/agent-loop-design.md` の機能 6](../../docs/designs/agent-loop-design.md#機能-6-slash-プロパティ--実装済み)。

### hooks（フックによる送信制御）

`hooks` にフックスクリプトを文字列または配列で指定すると、スケジュール発火のたびに
各フックの `check()` が呼ばれ、返された prompt がそれぞれ配送されます。パスの代わりに
`gitlab-issue-hook` のような名前を指定すると、インストール済みの `hooks/` を探索します。

```python
def check() -> str | None:
    ...  # str を返す→その内容を送信 / None を返す→今回はスキップ
```

- `hooks` を使う場合 `prompt` は省略できます（フックが内容を決めるため）。
- フック固有設定は `hook_config` に辞書で指定し、`check(config)` の
  `config["hook_config"]` へ渡されます。
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
| `resource-control-hook.py` | LLM へは送信せず、dashboard 共通の headless 入口で予算再配分と profile 適用を行う。 |
| `audit-calibrate-hook.py` | LLM へは送信せず、audit 収集後に `rates.per_cli` を実測中央値へ較正する。 |

GitLab 用の前二つは `gitlab-idd` スキルの `scripts/gl.py` を利用します。`GITLAB_TOKEN` を
設定し、必要に応じて環境変数（`AGENT_LOOP_GL_PY`, `AGENT_LOOP_GL_CWD`,
`AGENT_LOOP_ISSUE_LABELS` など）でパスやフィルター条件を上書きしてください。

### mapping（設定値の参照）

トップレベルの `mapping` にラベルごとの辞書を置くと、設定内の文字列で
`{{lookup <ラベル> <キー>}}` として参照できます。存在しないラベルまたはキーは設定エラーです。

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

- 共通設定: `~/.agents/agent-loop.yaml` / `.yml` / `.json`
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
