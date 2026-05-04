# 制御フローパターン集

ステートマシン設計で頻出する制御フローパターンのリファレンス。
基本パターン（ループ・エラーハンドリング等）は `SKILL.md` を参照。

## 目次

- [並列実行パターン（Fan-out/Fan-in）](#並列実行パターンfan-outfan-in)
- [ゲートステートパターン（検証ゲート）](#ゲートステートパターン検証ゲート)
- [ReActアンカリング（思考-行動-観察）](#reactアンカリング思考-行動-観察)
- [自己検証フィールド（出力前チェックリスト）](#自己検証フィールド出力前チェックリスト)
- [マイルストーンアンカー（中間結果の明示的保存）](#マイルストーンアンカー中間結果の明示的保存)
- [Saga（補償トランザクション）パターン](#saga補償トランザクションパターン)
- [パターン選択ガイド](#パターン選択ガイド)

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

## パターン選択ガイド

| 状況 | 適用するパターン |
|---|---|
| 「AとBを同時に / 並列で」 | Fan-out/Fan-in |
| 副作用が大きい操作の後 | ゲートステート |
| 複雑な判断・推論が必要 | ReActアンカリング |
| 出力の正確性が重要 | 自己検証フィールド |
| 長いワークフロー（5ステート以上） | マイルストーンアンカー |
| 複数ステップで一部失敗が許されない | Saga（補償トランザクション） |
| 繰り返し処理 | do-whileループ（SKILL.md参照） |
| 任意のステートからエラーへ飛ぶ | ワイルドカードトランジション |
