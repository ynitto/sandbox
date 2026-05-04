---
name: statemachine-use
description: 「ステートマシンを実行して」「ステートマシンを作成して」「YAMLワークフローを動かして」「ワークフローを回して」「エージェントループを起動して」などで発動。2つのモードを持つ。①作成モード: ユーザーが指示した手順をYAML+マークダウンとして .statemachine/{名前}/ に生成する。②実行モード: .statemachine/{名前}/ または指定パスのYAMLをLLM駆動・ハイブリッド方式で実行する。
metadata:
  version: 2.0.0
  tier: experimental
  category: workflow
  tags:
    - statemachine
    - yaml-workflow
    - agent-loop
    - hybrid-execution
---

# YAML ステートマシン スキル

YAMLと外部マークダウンファイルで定義されたLLM駆動ステートマシンを**作成・実行**します。

---

## モードの選択

| ユーザーの意図 | モード |
|---|---|
| 「〜という手順でステートマシンを作って」 | **作成モード** |
| 「〜を実行して」「〜を動かして」「YAMLを回して」 | **実行モード** |

---

## 作成モード

ユーザーが自然言語で説明した手順を `.statemachine/{名前}/` フォルダ以下のYAML+マークダウンに落とし込む。

### ステップ1: 利用可能なスキルを調査する

```bash
ls .github/skills/
```

出力されたスキル名を記録する。アクション定義でスキル呼び出しを活用できる場合に参照する。

### ステップ2: 手順を状態遷移として分解する

ユーザーの手順を分析し、以下の原則でステートを設計する:

**LLM読み飛ばし防止の設計原則（重要）**

1. **ルーティングロジックをアクションに書かない**  
   アクションは「何をするか」だけを記述する。「問題なければ承認し、問題があれば修正」のような分岐判断はアクションに書かず、トランジション条件に書く。

2. **出力形式を強制する**  
   条件が評価しやすいよう、アクションは特定のキーワードや形式での出力を要求する。  
   例: 「PASS / MINOR / MAJOR / CRITICAL のいずれかで評価を出力してください」

3. **ループはdo-whileパターンで実現する**  
   ループが必要な場合、アクションを先に実行してから条件で継続/終了を判断させる。ループ全体を先にアクションへ提示しない。コンテキスト変数（`retry_count`等）でカウントして上限を設ける。

4. **将来のステートをアクションにヒントとして含めない**  
   アクションプロンプトは現在のステートの作業のみを指示する。次に何が来るかをLLMに予告しない。

5. **アクションの末尾に単一指示を付与する**  
   各アクションmdファイルの末尾に必ず追記する:
   ```
   この指示に従ってタスクを実行してください。
   完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
   ```

6. **スキルを使ってLLMにアクションを実行させる**  
   スクリプトは原則作成しない。利用可能なスキル（Step1で確認）や他のClaude機能を呼び出す形でアクションを記述する。

### ステップ3: フォルダ構造を作成する

```
.statemachine/{名前}/
  workflow.yaml          # ステート・トランジション定義
  actions/              # 各ステートのアクション（マークダウン）
    {state_id}.md
  conditions/           # 複雑な条件（マークダウン）※シンプルな条件はYAMLインラインでよい
    {from}_to_{to}.md
```

### ステップ4: ファイルを生成する

**workflow.yaml の構造:**

```yaml
name: "ワークフロー名"
description: "説明"
initial_state: first_state
context:
  # ループカウンターなど必要な初期変数
  retry_count: 0
  max_retries: 3

config:
  max_steps: 30
  on_no_transition: "error"

states:
  first_state:
    description: "最初のステート"
    action_file: actions/first_state.md   # 外部ファイル参照
    output_key: first_result

  loop_state:
    description: "繰り返しステート"
    action_file: actions/loop_state.md

  done:
    description: "完了"
    action_file: actions/done.md
    terminal: true

transitions:
  - from: first_state
    to: loop_state
    condition: "最後の出力に SUCCESS という単語が含まれている"
    priority: 1
  - from: loop_state
    to: loop_state                        # ループ: 同じステートへ戻る
    condition_file: conditions/loop_state_to_loop_state.md
    priority: 1
  - from: loop_state
    to: done
    condition: "{{retry_count}} が {{max_retries}} 以上である"
    priority: 2
```

**actions/{state_id}.md の構造:**

```markdown
## [ステートID の作業]

（利用可能なスキルや具体的な指示を記述）

**入力:** {{input}}
**前のステートの出力:** {{last_output}}

（作業内容の詳細）

**出力形式:** 〇〇 / △△ / ×× のいずれかの単語のみで回答してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

**conditions/{from}_to_{to}.md の構造（複雑な条件のみ）:**

```markdown
以下の条件をYES/NOで評価してください:

- 最後の出力が「RETRY」で始まる、かつ
- {{retry_count}} が {{max_retries}} 未満である

両方を満たす場合のみ YES と回答してください。
```

---

## 実行モード

### 実行トリガー

ユーザーが名前または既存YAMLパスを指定して実行を要求した場合。

- 名前指定: `.statemachine/{名前}/workflow.yaml` を読み込む
- パス指定: 指定されたYAMLを直接読み込む

### Step 0: 準備

```bash
python .github/skills/statemachine-use/scripts/run_machine.py .statemachine/{名前}/workflow.yaml --dry-run
```

または名前で:
```bash
python .github/skills/statemachine-use/scripts/next_state.py {名前} --state {initial_state} --list-conditions
```

検証が通ったら `initial_state` を確認して実行を開始する。

### Step 1〜N: ステートループ（terminal まで繰り返す）

**現在のステートに入ったことを宣言する（毎ステート必須）:**

```
## [現在のステート: {state_id}]
```

**① 条件リストを取得する（Python）**

```bash
python .github/skills/statemachine-use/scripts/next_state.py .statemachine/{名前}/workflow.yaml \
  --state {現在のstate_id} --list-conditions
```

出力例:
```json
{
  "state": "classify",
  "conditions": [
    {"index": 0, "to": "handle_bug", "condition": "最後の出力がBUGという単語のみである"},
    {"index": 1, "to": "handle_feature", "condition": "最後の出力がFEATUREという単語のみである"}
  ]
}
```

**② アクションを実行する（LLM）**

`action` フィールド（または参照先mdファイル）のプロンプトを**そのまま**実行する。  
出力を `last_output` として記録する。

> **重要**: アクションを実行する前に条件を読んで結果を予測してはならない。アクションの出力が確定してから条件を評価する。

**③ 各条件を評価する（LLM）**

`last_output` に対して各条件を YES / NO で評価し、JSON を構築する:
```json
{"0": true, "1": false}
```

**④ 遷移先を確定する（Python）**

```bash
python .github/skills/statemachine-use/scripts/next_state.py .statemachine/{名前}/workflow.yaml \
  --state {現在のstate_id} --evals '{"0": true, "1": false}'
```

出力: 次の `state_id`、`NONE`（一致なし）、`TERMINAL`（終端）

**⑤ 遷移 or 終了**

- 次の `state_id` → そのステートへ移動して Step 1 に戻る
- `TERMINAL` → 実行完了、最終出力を表示
- `NONE` → `on_no_transition` 設定に従う（デフォルト: エラー）

---

## YAML スキーマ（クイックリファレンス）

```yaml
states:
  state_id:
    description: "ラベル"
    action: |          # インライン記述
      プロンプト
    action_file: actions/state_id.md   # 外部ファイル（action より優先）
    terminal: false
    output_key: "my_key"

transitions:
  - from: state_id
    to: other_state_id
    condition: |       # インライン条件
      自然言語条件
    condition_file: conditions/from_to_to.md  # 外部ファイル（condition より優先）
    priority: 1
```

全フィールドの仕様は `references/schema.md` を参照。

---

## ファイル参照の優先順位

アクション・条件のテキスト解決は以下の順で行う:

1. `action_file:` / `condition_file:` フィールド（明示的なファイルパス）
2. `action:` / `condition:` フィールドが `file:` で始まる場合（`file: actions/foo.md`）
3. `action:` / `condition:` フィールドのインラインテキスト
4. 自動探索: `actions/{state_id}.md` / `conditions/{from}_to_{to}.md` が存在すれば自動読み込み

---

## 外部実行: `run_machine.py`（CLI ランナー）

```bash
# 名前で実行（.statemachine/{name}/workflow.yaml を自動探索）
python .github/skills/statemachine-use/scripts/run_machine.py .statemachine/my_workflow/workflow.yaml --agent claude

# GitHub Copilot CLI
python .github/skills/statemachine-use/scripts/run_machine.py .statemachine/my_workflow/workflow.yaml --agent copilot
```

主要オプション:
```
--agent {claude,copilot,kiro,anthropic}  LLM バックエンド（デフォルト: claude）
--input TEXT                             初期入力テキスト
--context KEY=VALUE                      初期コンテキスト変数（繰り返し指定可）
--verbose                                遷移の詳細ログを表示
--dry-run                                検証のみ、実行しない
```

---

## LLM 安全設計チートシート

効果的なステートマシン定義のための設計パターン。

### アクションプロンプトの書き方

```markdown
## [state_id: 何をするステートか]

（具体的な作業指示。スキル名があれば活用する）

**入力:** {{input}}

（詳細な指示）

**出力形式:** PASS または FAIL の一語のみで回答してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

### ループパターン（do-while）

```yaml
context:
  iteration: 0
  max_iterations: 5

transitions:
  - from: work
    to: work          # 継続: 同じステートへ
    condition: "最後の出力にRETRYが含まれ、かつ {{iteration}} が {{max_iterations}} 未満"
    priority: 1
  - from: work
    to: done          # 終了: 完了または上限到達
    condition: "最後の出力にDONEが含まれる、または {{iteration}} が {{max_iterations}} 以上"
    priority: 2
```

### エラーハンドリング

```yaml
states:
  error:
    description: "エラーハンドラー"
    action_file: actions/error.md
    terminal: true

transitions:
  - from: "*"
    to: error
    condition: "最後の出力に FATAL ERROR または CRITICAL というフレーズが含まれている"
    priority: 100
```

### トランジション条件の書き方

- 明確に: 「最後の出力が 'YES' で始まる」（「肯定的に見える」はNG）
- コンテキストを使う: 「{{retry_count}} が 3 未満である」
- 排他的に: 優先度付きで条件が重複しないよう設計する

---

## フォルダ構成

- `.statemachine/{名前}/workflow.yaml` — ステート・トランジション定義
- `.statemachine/{名前}/actions/{state_id}.md` — アクションプロンプト
- `.statemachine/{名前}/conditions/{from}_to_{to}.md` — 複雑な条件プロンプト
- `scripts/engine.py` — コア非同期ステートマシンエンジン
- `scripts/run_machine.py` — CLI ランナー
- `scripts/next_state.py` — ハイブリッドインライン実行用 遷移計算スクリプト
- `references/schema.md` — 完全な YAML スキーマ仕様
- `examples/` — ワークフロー YAML サンプルファイル

---

## クイックスタート例（作成モードの出力イメージ）

**ユーザー**: 「コードレビューワークフローを作って: review_code」

**生成されるファイル:**

`.statemachine/review_code/workflow.yaml`:
```yaml
name: "コードレビュー"
initial_state: analyze
context:
  revision_count: 0
  max_revisions: 3

states:
  analyze:
    description: "コードを分析"
    action_file: actions/analyze.md
    output_key: analysis_result
  request_revision:
    description: "修正を依頼"
    action_file: actions/request_revision.md
    terminal: true
  approve:
    description: "承認"
    action_file: actions/approve.md
    terminal: true

transitions:
  - from: analyze
    to: approve
    condition: "analysis_result が PASS で始まる"
    priority: 1
  - from: analyze
    to: request_revision
    condition: "analysis_result が PASS 以外で始まる"
    priority: 2
```

`.statemachine/review_code/actions/analyze.md`:
```markdown
## [analyze: コード品質を分析する]

以下のコードを品質の観点で分析してください。

**対象コード:**
{{input}}

確認項目: バグ、コードの臭い、エラーハンドリング漏れ、パフォーマンス問題

**出力形式:** 最初の行に PASS / MINOR / MAJOR / CRITICAL のいずれか一語、その後に具体的な問題点を列挙してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```
