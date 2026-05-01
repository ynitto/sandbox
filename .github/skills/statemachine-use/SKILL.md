---
name: statemachine-use
description: 「ステートマシンを実行して」「YAMLワークフローを動かして」「ワークフローを回して」「エージェントループを起動して」「このYAMLを実行して」などで発動。YAML で定義した states/transitions ワークフローを LLM 駆動・ハイブリッド方式（アクション/条件判定は LLM、状態遷移は Python が確定）で実行するスキル。
metadata:
  version: 1.1.0
  tier: experimental
  category: workflow
  tags:
    - statemachine
    - yaml-workflow
    - agent-loop
    - hybrid-execution
---

# YAML ステートマシン スキル

YAMLファイルで定義された、LLM駆動のステートマシンを実行します。
各ステートはLLM経由で自然言語アクションを実行し、各トランジション条件もLLMが評価します。
新しいワークフローを追加するためのコード変更は不要です。

## YAML スキーマ（クイックリファレンス）

```yaml
name: "ワークフロー名"
description: "このワークフローの説明"
initial_state: state_id
context: {}           # オプション: 初期共有コンテキスト変数

states:
  state_id:
    description: "人が読めるラベル"
    action: |
      このステートでエージェントが実行すべき自然言語の指示。
      {{variable_name}} でコンテキスト変数を参照できます。
      エージェントはこれを実行して出力を生成します。
    terminal: false   # 終端ステートは true に設定（トランジション不要）

transitions:
  - from: state_id
    to: other_state_id
    condition: |
      エージェントが真偽で評価する自然言語条件。
      例: "ユーザーのリクエストに疑問符が含まれている"
      例: "前のステートの出力がエラーを示している"
    priority: 1       # 小さいほど先に評価（デフォルト: 0）
```

全オプションを含む完全なスキーマは `references/schema.md` を参照してください。

## 実行方法

ユーザーが「`path/to/workflow.yaml` を実行して」と伝えると、
エージェントが以下のハイブリッドプロトコルでインライン実行します。
**YAMLワークフローはファイルパスで渡すことを前提とします。**

**アクション実行・条件判定 → エージェントLLMが処理**  
**状態遷移の確定 → `scripts/next_state.py` が決定論的に処理**

### インライン実行プロトコル

#### Step 0: 準備

ファイルの存在と内容を確認し、ワークフローを検証する:
```bash
python scripts/run_machine.py path/to/workflow.yaml --dry-run
```

検証が通ったら、initial_state を確認して実行を開始する。

#### Step 1〜N: ステートループ（terminal まで繰り返す）

**① 条件リストを取得する（Python）**
```bash
python scripts/next_state.py path/to/workflow.yaml --state <現在のstate_id> --list-conditions
```
出力例:
```json
{
  "state": "classify",
  "conditions": [
    {"index": 0, "to": "handle_bug",      "condition": "The last output is exactly the word BUG"},
    {"index": 1, "to": "handle_feature",  "condition": "The last output is exactly the word FEATURE"},
    {"index": 2, "to": "handle_question", "condition": "The last output is exactly the word QUESTION"}
  ]
}
```

**② アクションを実行する（LLM）**

`action` フィールドのプロンプトを実行し、出力を `last_output` として記録する。

**③ 各条件を評価する（LLM）**

`last_output` に対して各条件を YES / NO で評価し、JSON を構築する:
```json
{"0": true, "1": false, "2": false}
```

**④ 遷移先を確定する（Python）**
```bash
python scripts/next_state.py path/to/workflow.yaml --state <現在のstate_id> --evals '{"0": true, "1": false, "2": false}'
```
出力: `handle_bug`（次のstate_id）、`NONE`（一致なし）、`TERMINAL`（終端）

**⑤ 遷移 or 終了**

- 次のstate_idが返されたら → そのステートへ移動して Step 1 に戻る
- `TERMINAL` が返されたら → 実行完了
- `NONE` が返されたら → `on_no_transition` 設定に従う（デフォルト: エラー）

---

### `next_state.py` オプション

```
workflow              ワークフロー YAML ファイルのパス
--state STATE         現在のステートID（必須）
--evals JSON          条件評価結果 JSON（--list-conditions なしの場合は必須）
--list-conditions     評価すべき条件リストを表示して終了（eval前の確認用）
```

---

## 外部実行: `run_machine.py`（CLI ランナー）

LLM バックエンドを選択してコマンドラインから実行できます:

```bash
# Claude Code CLI（デフォルト）
python scripts/run_machine.py path/to/workflow.yaml --agent claude

# GitHub Copilot CLI
python scripts/run_machine.py path/to/workflow.yaml --agent copilot

# Kiro CLI
python scripts/run_machine.py path/to/workflow.yaml --agent kiro

# Anthropic Python SDK（ANTHROPIC_API_KEY 必須）
python scripts/run_machine.py path/to/workflow.yaml --agent anthropic --model claude-sonnet-4-20250514
```

主要オプション:
```
--agent {claude,copilot,kiro,anthropic}  LLM バックエンド（デフォルト: claude）
--model MODEL                          モデル ID（claude / anthropic のみ有効）
--input TEXT                           初期入力テキスト
--context KEY=VALUE                    初期コンテキスト変数（繰り返し指定可）
--verbose                              遷移の詳細ログを表示
--dry-run                              検証のみ、実行しない
--output-json                          最終結果を JSON で出力
```

## 実行モデル

```
[initial_state]
     │
     ▼
  state.action を LLM 経由で実行 → 出力をコンテキストに保存
     │
     ▼
  各出力トランジション条件を LLM で評価（priority 順）
     │
     ├── 条件 TRUE → 対象ステートへ移動
     └── 条件が一致しない → ERROR またはループ（設定可能）
```

**コンテキストの蓄積**: 各ステートの出力は `context["last_output"]` と
`context["history"][state_id]` に格納されます。アクションは以前の出力を参照できます。

**終端ステート**: `terminal: true` のステートは実行を終了し、
蓄積されたコンテキストを結果として返します。

## 効果的なステートマシンの書き方

### アクションプロンプト
- 具体的に記述: 「ユーザーの意図を question, complaint, request のいずれかに分類してください」
- コンテキストを参照: 「分類結果 {{history.classify}} を踏まえて返答を作成してください」
- 出力を制約: 「YES または NO の一語のみで回答してください」

### トランジション条件
- 曖昧にしない: 「最後の出力が 'YES' で始まる」（「肯定的に見える」はNG）
- コンテキストを活用: 「変数 {{retry_count}} が 3 未満である」
- 条件を連鎖: `priority` で評価順序を制御

### エラーハンドリング
```yaml
states:
  error:
    description: "エラーハンドラー"
    action: "{{last_output}} に基づいて何が問題だったかを説明し、復旧手順を提案してください"
    terminal: true

transitions:
  - from: any_state
    to: error
    condition: "最後の出力に ERROR または FAILED という単語が含まれている"
    priority: 99   # 通常のトランジションの後に評価
```

## ファイル構成

- `scripts/engine.py` — コア非同期ステートマシンエンジン
- `scripts/run_machine.py` — CLI ランナー（`--agent claude|copilot|kiro|anthropic` で LLM 選択）
- `scripts/next_state.py` — ハイブリッドインライン実行用 遷移計算スクリプト
- `references/schema.md` — 完全な YAML スキーマ仕様
- `examples/` — ワークフロー YAML サンプルファイル

## クイックスタート例

```yaml
# examples/issue_triage.yaml
name: "イシュートリアージ"
initial_state: classify

states:
  classify:
    description: "受け取ったイシューを分類"
    action: |
      このイシューを BUG, FEATURE, QUESTION のいずれか一つに分類してください。
      イシュー: {{input}}
      カテゴリの単語のみで回答してください。

  handle_bug:
    description: "バグ報告の処理"
    action: |
      このイシューはバグ (BUG) として分類されました。
      元のイシュー: {{input}}
      Summary、Steps to Reproduce、Expected vs Actual を含む構造化バグレポートを作成してください。
    terminal: true

  handle_feature:
    description: "機能リクエストの処理"
    action: |
      このイシューは機能リクエスト (FEATURE REQUEST) として分類されました。
      元のイシュー: {{input}}
      この機能の受け入れ条件リストを作成してください。
    terminal: true

  handle_question:
    description: "質問の処理"
    action: |
      このイシューは質問 (QUESTION) として分類されました。
      元のイシュー: {{input}}
      簡潔に質問に回答してください。
    terminal: true

transitions:
  - from: classify
    to: handle_bug
    condition: "The last output is exactly the word BUG"
  - from: classify
    to: handle_feature
    condition: "The last output is exactly the word FEATURE"
  - from: classify
    to: handle_question
    condition: "The last output is exactly the word QUESTION"
```

実行: `python scripts/run_machine.py examples/issue_triage.yaml --input "モバイルでログインボタンが動作しない"`
