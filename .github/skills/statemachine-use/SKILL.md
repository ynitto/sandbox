---
name: statemachine-use
description: 「ステートマシンを実行して」「ステートマシンを作成/作って」「YAMLワークフローを動かして」「ワークフローを回して」「エージェントループを起動して」「このYAMLを実行して」などで発動。作成モード（手順を.statemachine/{名前}/に生成）と実行モード（YAMLをLLM駆動で実行）を持つ。
metadata:
  version: 2.1.0
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

**LLM読み飛ばし防止の設計原則（重要）**

1. **ルーティングロジックをアクションに書かない** — 分岐判断はトランジション条件に書く
2. **出力形式を強制する** — 条件が評価しやすいキーワード出力を要求する（例: `PASS / FAIL`）
3. **将来のステートをヒントとして含めない** — アクションは現在のステートの作業のみを指示する
4. **アクションの末尾に単一指示を付与する** — 全アクションmdの末尾に必ず下記を追記する:
   ```
   この指示に従ってタスクを実行してください。
   完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
   ```
5. **スクリプトは原則作成しない** — スキル（ステップ1で確認）や他のAI機能でアクションを実行する

**パターンの自動検出** — 詳細テンプレートは `references/patterns.md` を参照:

| 手順の特徴 | 適用するパターン |
|---|---|
| 「同時に」「並列で」「〜と〜を一緒に」 | Fan-out/Fan-in |
| 処理後に次があるか確認してループ | ContinueAsNew Loop |
| 副作用の大きい操作（変更・デプロイ等）の後 | ゲートステート |
| 複雑な判断・推論を含むステート | ReActアンカリング |
| 「失敗したら元に戻す」「ロールバック」 | Saga |
| 5ステート以上の長いワークフロー | マイルストーンアンカー |

### ステップ3: フォルダ構造を作成する

```
.statemachine/{名前}/
  workflow.yaml
  actions/{state_id}.md
  conditions/{from}_to_{to}.md   # 複雑な条件のみ。単純な条件はYAMLインラインでよい
```

### ステップ4: ファイルを生成する

**workflow.yaml** — 全フィールドの仕様は `references/schema.md` を参照:

```yaml
name: "ワークフロー名"
initial_state: first_state
context:
  # 初期変数（ループカウンター等）
config:
  max_steps: 30

states:
  state_id:
    description: "ラベル"
    action_file: actions/state_id.md   # 外部ファイル参照（推奨）
    output_key: result_key             # 任意: context に名前付き保存
    terminal: false

transitions:
  - from: state_id
    to: other_id
    condition: "自然言語条件"           # or condition_file: conditions/...md
    priority: 1
```

**actions/{state_id}.md:**

```markdown
## [state_id: 何をするか]

（スキル呼び出しや具体的な指示）

**入力:** {{input}}
**前のステートの出力:** {{last_output}}

**出力形式:** XXX または YYY の一語のみで回答してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

**conditions/{from}_to_{to}.md（複雑な条件のみ）:**

```markdown
以下の条件をYES/NOで評価してください:
- {{retry_count}} が {{max_retries}} 未満である、かつ
- 最後の出力が RETRY で始まる

両方を満たす場合のみ YES と回答してください。
```

### 作成例

```yaml
# .statemachine/review_code/workflow.yaml
name: "コードレビュー"
initial_state: analyze
states:
  analyze:
    action_file: actions/analyze.md
    output_key: analysis_result
  approve:
    action_file: actions/approve.md
    terminal: true
  request_revision:
    action_file: actions/request_revision.md
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

```markdown
<!-- .statemachine/review_code/actions/analyze.md -->
## [analyze: コード品質を分析する]

以下のコードを品質の観点で分析してください。
**対象コード:** {{input}}

確認項目: バグ、コードの臭い、エラーハンドリング漏れ、パフォーマンス問題

**出力形式:** 最初の行に PASS / MINOR / MAJOR / CRITICAL のいずれか一語、その後に問題点を列挙してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## 実行モード

### ⛔ ハーネス実行プロトコル — 禁止行動（違反時は即座に停止して再確認）

| 禁止行動 | 代替行動 |
|---|---|
| アクション実行前に条件リストを取得する | ① 実行 → 出力確定 → ② 条件取得 の順を守る |
| 現在のステート以外の作業を実行する | 現在のステートの作業のみ実行する |
| `## [現在のステート: {state_id}]` 宣言を省略する | 毎ステートの冒頭で必ず宣言する |
| 条件を評価せずに遷移先を独断で決める | 必ず ④ の Python スクリプトで遷移先を確定する |
| 複数ステートをまとめて実行する | 1ステート = 1ターンを厳守する |

---

### Step 0: 検証と開始ステートの取得

```bash
# ワークフローの検証
python .github/skills/statemachine-use/scripts/run_machine.py .statemachine/{名前}/workflow.yaml --dry-run

# 開始ステートの取得
python .github/skills/statemachine-use/scripts/next_state.py {名前} --initial-state
```

出力された `state_id` を現在のステートとして実行を開始する。

### Step 1〜N: ステートループ（terminal まで繰り返す）

**現在のステートに入ったことを宣言する（毎ステート必須）:**

```
## [現在のステート: {state_id}]
```

**① アクションを実行する（LLM）**

現在のステートのアクションプロンプトを実行し、出力を `last_output` として記録する。

> **重要**: アクション実行前に条件を確認してはならない。出力が確定してから条件リストを取得する。

**② 条件リストを取得する（Python）**

```bash
python .github/skills/statemachine-use/scripts/next_state.py {名前} \
  --state {現在のstate_id} --list-conditions \
  --last-output "{last_outputの第1行}"
```

`needs_llm_eval: false` の条件は `condition_rule` で自動評価済み（LLM評価不要）。
`needs_llm_eval: true` の条件のみ次のステップで評価する。

**③ 各条件を評価する（LLM）**

`needs_llm_eval: true` の条件のみ `last_output` に対して YES / NO で評価し、JSON を構築する:
```json
{"1": false}
```
（`needs_llm_eval: false` の条件インデックスは省略可。`--evals` 渡し時に自動上書きされる）

**④ 遷移先を確定する（Python）**

```bash
python .github/skills/statemachine-use/scripts/next_state.py {名前} \
  --state {現在のstate_id} --evals '{"1": false}' \
  --last-output "{last_outputの第1行}"
```

出力: 次の `state_id`、`NONE`（一致なし）、`TERMINAL`（終端）

> `condition_rule` がある条件は `--last-output` から自動評価され、`--evals` の値を上書きする。

**⑤ 完了を記録する**

```
## [ステート {state_id} 完了]
- 出力: {last_outputの第1行}
- 遷移先: {次のstate_id または TERMINAL}
```

**⑥ 遷移 or 終了**

- 次の `state_id` → そのステートへ移動して Step 1 に戻る
- `TERMINAL` → 実行完了、最終出力を表示
- `NONE` → `on_no_transition` 設定に従う（デフォルト: エラー）
