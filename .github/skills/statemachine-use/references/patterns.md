# 制御フローパターン集

ステートマシン設計で頻出する制御フローパターンのリファレンス。
基本パターン（ループ・エラーハンドリング等）は `SKILL.md` を参照。

## 目次

- [繰り返しパターン（ContinueAsNew Loop）](#繰り返しパターンcontinueasnew-loop)
- [並列実行パターン（Fan-out/Fan-in）](#並列実行パターンfan-outfan-in)
- [ゲートステートパターン（検証ゲート）](#ゲートステートパターン検証ゲート)
- [ReActアンカリング（思考-行動-観察）](#reactアンカリング思考-行動-観察)
- [自己検証フィールド（出力前チェックリスト）](#自己検証フィールド出力前チェックリスト)
- [マイルストーンアンカー（中間結果の明示的保存）](#マイルストーンアンカー中間結果の明示的保存)
- [Saga（補償トランザクション）パターン](#saga補償トランザクションパターン)
- [パターン選択ガイド](#パターン選択ガイド)

---

## 繰り返しパターン（ContinueAsNew Loop）

Temporal.io の `workflow.continueAsNew()` に由来する名称。処理を1件完了するたびに実行コンテキストを「新しい状態でやり直す」ようにループを継続し、履歴を蓄積させずに長期実行を実現するパターン。

ステートマシンでは「処理ステート → 継続確認ステート → 次があれば処理ステートへ戻る」という2ステート構成で表現する。各イテレーションで `process` ステートは必ず新鮮な入力を受け取り、前回の出力を引き継がない。

```yaml
context:
  processed_count: 0     # 処理済み件数（無限ループ防止用）
  max_iterations: 50     # 安全上限

states:
  process:
    description: "1件処理する"
    action_file: actions/process.md
    output_key: process_result

  check_next:
    description: "次の処理対象があるか確認する"
    action_file: actions/check_next.md
    output_key: next_decision

  all_done:
    description: "全件処理完了"
    action_file: actions/all_done.md
    terminal: true

transitions:
  - from: process
    to: check_next
    condition: "最後の出力が DONE で始まる"
    priority: 1

  - from: check_next
    to: process           # ContinueAsNew: 新しい入力で処理ステートを再起動
    condition: "next_decision に NEXT が含まれ、かつ {{processed_count}} が {{max_iterations}} 未満"
    priority: 1
  - from: check_next
    to: all_done
    condition: "next_decision に NO_MORE が含まれる、または {{processed_count}} が {{max_iterations}} 以上"
    priority: 2
```

**`actions/process.md`:**

```markdown
## [process: 1件処理する]

処理済み件数: {{processed_count}}

[1件分の処理内容を記述]

処理が完了したら `DONE` を最初の行に出力し、処理結果の概要を続けてください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

**`actions/check_next.md`:**

```markdown
## [check_next: 次の処理対象があるか確認する]

直前の処理結果:
{{process_result}}

次に処理すべき対象があるかどうか確認してください。

[次の対象の有無を確認する方法を記述。例: ユーザーへの問い合わせ、未処理リストの参照、キューの確認 等]

**出力形式:**
- 次の対象がある場合: `NEXT: [次の対象の概要]` を最初の行に出力する
- 全て完了の場合: `NO_MORE` を最初の行に出力する

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## 並列実行パターン（Fan-out/Fan-in）

ユーザーが「同時に」「並列で」「〜と〜を一緒に」を指示した場合、1つの fan-out ステートでサブエージェントを同時起動し、fan-in ステートで結果を集約する。

### workflow.yaml

```yaml
states:
  fan_out:
    description: "並列タスクを起動"
    action_file: actions/fan_out.md
    output_key: parallel_results

  fan_in:
    description: "並列結果を集約して判定"
    action_file: actions/fan_in.md
    output_key: aggregated_result

transitions:
  - from: fan_out
    to: fan_in
    condition: "最後の出力に ALL_DONE が含まれている"
  - from: fan_in
    to: done
    condition: "aggregated_result が OVERALL_PASS で始まる"
  - from: fan_in
    to: error
    condition: "aggregated_result が OVERALL_FAIL で始まる"
```

### `actions/fan_out.md` テンプレート

```markdown
## [fan_out: 以下のタスクを並列サブエージェントで同時に実行する]

> ⛔ STOP — 以下のサブエージェントを**同一ターン内で並列起動**してください。
> 順番に実行するのではなく、全タスクを1つのメッセージで同時に起動します。

### サブエージェント 1: [タスク名]

[タスク1の詳細な指示]

期待する出力: TASK1_RESULT: [結果]

---

### サブエージェント 2: [タスク名]

[タスク2の詳細な指示]

期待する出力: TASK2_RESULT: [結果]

---

全サブエージェントが完了したら、以下の形式でまとめてください:

TASK1_RESULT: [タスク1の結果]
TASK2_RESULT: [タスク2の結果]
ALL_DONE

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

### `actions/fan_in.md` テンプレート

```markdown
## [fan_in: 並列タスクの結果を集約して判定する]

以下の並列タスクの結果を確認してください:

{{parallel_results}}

全てのタスクが成功していれば OVERALL_PASS、1つでも失敗があれば OVERALL_FAIL を最初の行に出力し、
その後に各タスクの結果サマリを列挙してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## ゲートステートパターン（検証ゲート）

副作用が大きい操作（ファイル変更・デプロイ・API呼び出し等）の後に挿入する検証ステート。
問題があれば補償ステートへ、問題なければ次のステートへ進む。

### workflow.yaml

```yaml
states:
  do_work:
    description: "メイン作業"
    action_file: actions/do_work.md
    output_key: work_output

  gate_verify:
    description: "出力を検証するゲート"
    action_file: actions/gate_verify.md

  compensate:
    description: "補償処理（ロールバック等）"
    action_file: actions/compensate.md
    terminal: true

transitions:
  - from: do_work
    to: gate_verify
    condition: "最後の出力が DONE で始まる"
    priority: 1
  - from: gate_verify
    to: next_step
    condition: "最後の出力が GATE_PASS で始まる"
    priority: 1
  - from: gate_verify
    to: compensate
    condition: "最後の出力が GATE_FAIL で始まる"
    priority: 2
```

### `actions/gate_verify.md` テンプレート

```markdown
## [gate_verify: 前ステートの出力を検証する]

前ステートの作業結果:
{{work_output}}

以下のチェックリストを全て確認してください。全て OK なら GATE_PASS、1つでも NG なら GATE_FAIL を最初の行に出力してください。

**チェックリスト:**
- [ ] [確認項目1]
- [ ] [確認項目2]
- [ ] [確認項目3]

出力形式: GATE_PASS または GATE_FAIL の後に、各チェック項目の結果を記載してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## ReActアンカリング（思考-行動-観察）

複雑な判断を伴うアクションで LLM が結論に飛びつかないよう、思考→行動→観察の3段階を強制する。

```markdown
## [complex_action: 複雑な判断が必要なステート]

以下の手順を**順番に**実行してください:

### 🔍 思考（Reason）
このタスクを実行するにあたって:
- 現在の状況: {{last_output}}
- 何を確認する必要があるか
- リスクや注意点は何か

### ⚡ 行動（Act）
上記の思考に基づいて、以下を実行してください:
[具体的な作業指示]

### 👁️ 観察（Observe）
実行後、以下を確認してください:
- 期待通りの結果が得られたか
- エラーや警告はなかったか
- 次のステートに進む条件を満たしているか

**最終出力:** 観察の結果に基づいて PROCEED または RETRY の一語を最初の行に出力してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## 自己検証フィールド（出力前チェックリスト）

アクションの末尾に自己チェックリストを追加することで、LLMが出力前に自分の作業を検証する。

```markdown
（メイン作業指示）

**出力形式:** [指定の形式]

**出力前の自己チェック（全て確認してから出力すること）:**
- [ ] 出力が指定の形式に従っているか
- [ ] 前のステートのコンテキスト（{{last_output}}）を正しく参照したか
- [ ] 判断の根拠は明確か
- [ ] 副作用や漏れはないか

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

## マイルストーンアンカー（中間結果の明示的保存）

長いワークフローでは `output_key` を使って重要な中間結果を名前付きで保存する。
後続のステートがコンテキストを失っても `{{output_key}}` で参照できる。

```yaml
states:
  analyze:
    action_file: actions/analyze.md
    output_key: analysis_result     # context.analysis_result として保存

  plan:
    action_file: actions/plan.md
    output_key: plan_result
    # action 内で {{analysis_result}} を参照できる

  execute:
    action_file: actions/execute.md
    output_key: execution_result
    # action 内で {{plan_result}} と {{analysis_result}} を参照できる
```

各アクションmdファイルで明示的に参照:

```markdown
**前フェーズの分析結果:** {{analysis_result}}
**承認済みプラン:** {{plan_result}}

上記のプランに従って実行してください。
```

---

## Saga（補償トランザクション）パターン

複数ステップにわたる操作で一部が失敗した場合に補償アクションで前の変更を取り消す。

```yaml
context:
  completed_steps: ""   # 完了したステップのログ

states:
  step_a:
    action_file: actions/step_a.md
    output_key: step_a_result

  step_b:
    action_file: actions/step_b.md
    output_key: step_b_result

  compensate_b:
    description: "step_b を取り消す"
    action_file: actions/compensate_b.md

  compensate_a:
    description: "step_a を取り消す"
    action_file: actions/compensate_a.md
    terminal: true

transitions:
  - from: step_a
    to: step_b
    condition: "step_a_result が SUCCESS で始まる"
    priority: 1
  - from: step_a
    to: compensate_a        # step_a 失敗: step_a だけロールバック
    condition: "step_a_result が FAILURE で始まる"
    priority: 2
  - from: step_b
    to: done
    condition: "step_b_result が SUCCESS で始まる"
    priority: 1
  - from: step_b
    to: compensate_b        # step_b 失敗: b→a の順でロールバック
    condition: "step_b_result が FAILURE で始まる"
    priority: 2
  - from: compensate_b
    to: compensate_a
    condition: "最後の出力に COMPENSATED が含まれている"
```

---

## ファセットプロンプティング（アクション品質標準化）

TAKTファセット設計思想に基づき、actionプロンプトを5つの独立したファセットに分解して品質を安定させる。
特に **Output Contract**（出力契約）を明確にすることで、`condition_rule` の `startswith` 判定が確実に機能するようになる。

### 5ファセット標準テンプレート

```markdown
## [state_id: ステートの目的を一文で]

**Persona（役割）:** あなたは[役割]として振る舞ってください。

**Policy（品質規約）:**
- 出力の第1行は必ず [KEYWORD_A] または [KEYWORD_B] の一語のみにすること
- 現在のステートの作業のみを行い、次のステップを先読みしないこと
- スコープ外の変更・追加機能は行わないこと

**Instructions（手順）:**
1. [具体的な作業ステップ1]
2. [具体的な作業ステップ2]
3. 完了後、Output Contract の形式で出力する

**Knowledge（参照コンテキスト）:**
- 入力: {{input}}
- 直前のステート出力: {{last_output}}

**Output Contract（出力契約）:**
\`\`\`
第1行: [KEYWORD_A] または [KEYWORD_B] の一語のみ（説明・修飾語を付けてはならない）
第2行以降: [根拠・詳細]
\`\`\`

**⚠️ 出力前の自己チェック:**
- [ ] 第1行が [KEYWORD_A] / [KEYWORD_B] の一語になっているか
- [ ] 現在のステート以外の判断・作業を含んでいないか

この指示に従ってタスクを実行してください。
完了後、Output Contract の形式のみで出力してください。次のステップは別途指示されます。
```

**workflow.yaml での対応**:

```yaml
states:
  analyze:
    action_file: actions/analyze.md
    output_key: analysis_result
    output_validator: "startswith:PASS,FAIL,MINOR,MAJOR,CRITICAL"  # Output Contract と対応
    max_retries: 2  # 形式違反時にリトライ

transitions:
  - from: analyze
    to: approve
    condition_rule: "startswith:analysis_result:PASS"  # Output Contract に依存した決定論的評価
    priority: 1
```

**効果**: Output Contract で第1行のキーワードが保証されるため、`condition_rule` の `startswith` 判定がLLM評価なしで安定して機能する。

---

## ゲート条件テーブルパターン

> **TAKT設計思想 — ルールベーストランジション制御**: 遷移の判断はAIの自由意思ではなく、
> 明示的なルールに基づいて行う。各条件は機械的に検証可能（出力のstartswith・contextの値）であり、曖昧な判断を排除する。

長いワークフローの終盤や副作用の大きい操作の前に挿入する。ゲートステートパターンの強化版で、
遷移条件を「テーブル」として明示し、全条件のAND評価で遷移を制御する。

### workflow.yaml

```yaml
states:
  gate_final:
    description: "最終ゲート: 全条件をAND評価して遷移を決定する"
    action_file: actions/gate_final.md
    output_key: gate_result

transitions:
  - from: gate_final
    to: complete
    condition_rule: "startswith:gate_result:GATE_PASS"   # 決定論的評価
    priority: 1
  - from: gate_final
    to: error_handler
    condition_rule: "not-startswith:gate_result:GATE_PASS"
    priority: 2
```

### `actions/gate_final.md` テンプレート

```markdown
## [gate_final: 全条件をテーブルで検証する]

**Persona:** あなたは厳格なゲートキーパーとして、全条件を客観的に検証します。

**Policy:**
- 各条件を独立して評価すること（前の条件の結果で他の評価を変えない）
- 1つでも条件が不適合なら GATE_FAIL を出力すること
- 条件番号を省略しないこと

**Instructions:**
1. 以下のゲート条件テーブルを上から順に評価する
2. 各条件に「適合 ✅ / 不適合 ❌」と理由を記入する
3. 全条件の評価が完了したら集約判定を出力する

**Knowledge:**
{{前フェーズの出力キー変数}}

**ゲート条件テーブル（全条件をANDで評価）:**

| # | 条件 | 検証方法 |
|---|------|---------|
| 1 | [条件1] | [何を見て確認するか] |
| 2 | [条件2] | [何を見て確認するか] |

**Output Contract:**
\`\`\`
第1行: GATE_PASS または GATE_FAIL の一語のみ
第2行以降:
| # | 条件 | 結果 | 理由 |
|---|------|------|------|
不適合条件がある場合: 不適合 N 件: [条件番号リスト]
\`\`\`

**⚠️ 出力前の自己チェック:**
- [ ] 全条件を評価したか
- [ ] 1件でも❌があれば GATE_FAIL を出力したか
- [ ] 第1行が GATE_PASS / GATE_FAIL の一語になっているか

この指示に従ってタスクを実行してください。
完了後、Output Contract の形式のみで出力してください。次のステップは別途指示されます。
```

---

## パターン選択ガイド

| 状況 | 適用するパターン |
|---|---|
| 「AとBを同時に / 並列で」 | Fan-out/Fan-in |
| 副作用が大きい操作の後 | ゲートステート |
| 複雑な判断・推論が必要 | ReActアンカリング |
| 出力の正確性が重要 | 自己検証フィールド |
| 長いワークフロー（5ステート以上） | マイルストーンアンカー |
| 複数ステップで一部失敗が許されない | Saga（補償トランザクション） |
| 繰り返し処理（なくなるまで継続） | ContinueAsNew Loop |
| 任意のステートからエラーへ飛ぶ | ワイルドカードトランジション |
| 出力形式を安定させたい | ファセットプロンプティング |
| 重要な遷移条件をLLMに依存させたくない | ゲート条件テーブルパターン + condition_rule |
