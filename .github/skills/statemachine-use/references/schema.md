# YAML ステートマシン スキーマ リファレンス

ワークフロー YAML ファイルの完全な仕様です。

## 目次

- [トップレベルフィールド](#トップレベルフィールド)
- [ステート定義](#ステート定義)
- [トランジション定義](#トランジション定義)
- [ワイルドカードトランジション](#ワイルドカードトランジション)
- [Config ブロック](#config-ブロック)
- [コンテキスト変数リファレンス](#コンテキスト変数リファレンス)
- [完全サンプル](#完全サンプル)

## トップレベルフィールド

| フィールド | 型 | 必須 | 説明 |
|-------|------|----------|-------------|
| `name` | 文字列 | はい | ワークフローの表示名 |
| `description` | 文字列 | いいえ | ワークフローの説明 |
| `initial_state` | 文字列 | はい | 開始ステートのID |
| `context` | オブジェクト | いいえ | 初期コンテキストのキーと値のペア |
| `config` | オブジェクト | いいえ | エンジン設定の上書き |
| `states` | オブジェクト | はい | state_id → ステート定義のマップ |
| `transitions` | リスト | はい | トランジション定義のリスト |

## ステート定義

```yaml
states:
  my_state:
    description: "短い人が読めるラベル"
    action: |
      このステートに入ったときに LLM へ送るプロンプト。
      テンプレート変数をサポート: {{variable_name}}
      利用可能な組み込み変数:
        {{input}}           - マシンに渡された元の入力
        {{last_output}}     - 最後に実行されたステートの出力
        {{current_state}}   - このステートのID
        {{step_count}}      - 発生したトランジションの数
        {{history.STATE_ID}} - 特定の名前付きステートの出力
    action_file: actions/my_state.md   # 外部ファイル参照（action より優先）
    terminal: false
    on_enter: "オプション: action に前置される追加指示"
    on_exit: "オプション: action の後、トランジション前に実行される指示"
    output_key: "my_key"  # このステートの出力を context.my_key にも格納
```

### ステートフィールド

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-------|------|----------|---------|-------------|
| `description` | 文字列 | いいえ | ステートID | 人が読めるラベル |
| `action` | 文字列 | はい* | — | このステートの LLM プロンプト。`file: path` 形式でファイル参照も可。*終端ステートでは不要 |
| `action_file` | 文字列 | いいえ | — | アクションを外部マークダウンファイルで指定（`action` より優先）。workflow.yaml からの相対パス |
| `terminal` | 真偽値 | いいえ | false | true の場合、ここで実行終了 |
| `on_enter` | 文字列 | いいえ | — | action プロンプトに前置されるプレフィックス |
| `on_exit` | 文字列 | いいえ | — | action 後のプロンプト（出力は格納されるがルーティングには使用しない） |
| `output_key` | 文字列 | いいえ | — | `context[output_key]` にも出力を格納 |
| `max_retries` | 整数 | いいえ | 0 | トランジション評価が失敗した場合の action のリトライ回数 |

### アクションの自動探索

`action` も `action_file` も指定されていない場合、`actions/{state_id}.md` が存在すれば自動で読み込む。

## トランジション定義

```yaml
transitions:
  - from: source_state_id    # 任意のステートからは "*" を使用
    to: target_state_id
    condition: |
      最後のステートの出力に対して評価する自然言語条件。
      LLM がこの条件に対して YES か NO で回答します。
      
      良い条件の例:
        "最後の出力に ERROR という単語が含まれている"
        "分類結果が BUG または FEATURE である"
        "{{retry_count}} が 2 より大きい"
        "前のステートが JSON オブジェクトを生成した"
    condition_file: conditions/source_to_target.md  # 外部ファイル参照（condition より優先）
    priority: 0
    description: "このトランジションの任意ラベル"
```

### トランジションフィールド

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-------|------|----------|---------|-------------|
| `from` | 文字列 | はい | — | 元ステートのID。ワイルドカードは `"*"` |
| `to` | 文字列 | はい | — | 遷移先ステートのID |
| `condition` | 文字列 | はい* | — | 自然言語条件（LLM が YES/NO で評価）。`file: path` 形式も可。*`condition_file` がある場合は不要 |
| `condition_file` | 文字列 | いいえ | — | 条件を外部マークダウンファイルで指定（`condition` より優先）。workflow.yaml からの相対パス |
| `priority` | 整数 | いいえ | 0 | 評価順序（小さいほど先） |
| `description` | 文字列 | いいえ | — | 人が読めるラベル |

### 条件の自動探索

`condition` も `condition_file` も指定されていない場合、`conditions/{from}_to_{to}.md` が存在すれば自動で読み込む（`from` が `*` の場合は `wildcard_to_{to}.md`）。

### ワイルドカードトランジション

任意のステートから適用されるトランジションを作成するには `from: "*"` を使用します:

```yaml
transitions:
  - from: "*"
    to: error
    condition: "最後の出力に 'FATAL ERROR' というフレーズが含まれている"
    priority: 100  # 通常のトランジションの後に評価
```

## Config ブロック

```yaml
config:
  max_steps: 50          # 強制停止までの最大ステート遷移数
  on_max_steps: "error"  # "error" | "stop" | ジャンプ先 state_id
  on_no_transition: "error"  # 条件が一致しない場合の動作
  verbose: false         # 各トランジションの推論をログ出力
  condition_model: "your-model-id"  # 条件評価に使用するモデルID（省略時はエージェントのデフォルトモデルを使用）
  action_model: "your-model-id"     # アクション実行に使用するモデルID（省略時はエージェントのデフォルトモデルを使用）
```

## Context Variable Reference

In `action` and `condition` strings, use `{{variable}}` syntax:

| Variable | Description |
|----------|-------------|
| `{{input}}` | Original input to the machine |
| `{{last_output}}` | Most recent state output |
| `{{current_state}}` | Current state ID |
| `{{step_count}}` | Number of completed transitions |
| `{{history.STATE_ID}}` | Stored output from state STATE_ID |
| `{{context.KEY}}` | Any custom context variable |
| `{{output_key}}` | Any state output stored with `output_key:` |

## Complete Example

```yaml
name: "Code Review Pipeline"
description: "Automated code review with iterative improvement"
initial_state: analyze
context:
  max_revisions: 3
  revision_count: 0

config:
  max_steps: 30
  verbose: true

states:
  analyze:
    description: "Analyze the submitted code"
    action: |
      Analyze this code for quality issues:
      {{input}}
      
      Identify: bugs, code smells, missing error handling, performance issues.
      Output a severity rating: PASS, MINOR, MAJOR, or CRITICAL.
      Then list specific issues found.
    output_key: analysis_result

  request_revision:
    description: "Request code revision"
    action: |
      Based on this analysis:
      {{analysis_result}}
      
      Write a clear, actionable revision request for the developer.
      Be specific about what needs to change and why.
    terminal: true

  approve:
    description: "Approve the code"
    action: |
      The code review is complete. Analysis:
      {{analysis_result}}
      
      Write an approval message confirming the code meets quality standards.
    terminal: true

  escalate:
    description: "Escalate critical issues"
    action: |
      CRITICAL issues were found that require immediate attention:
      {{analysis_result}}
      
      Write an escalation notice for the team lead.
    terminal: true

transitions:
  - from: analyze
    to: approve
    condition: "The analysis_result starts with PASS"
    priority: 1

  - from: analyze
    to: escalate
    condition: "The analysis_result starts with CRITICAL"
    priority: 2

  - from: analyze
    to: request_revision
    condition: "The analysis_result starts with MINOR or MAJOR"
    priority: 3
```
