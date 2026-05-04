# 制御フローパターン集

ステートマシン設計で頻出する制御フローパターンのリファレンス。
基本パターン（ループ・エラーハンドリング等）は `SKILL.md` を参照。

## 目次

- [繰り返しパターン（do-while / キュードレイン）](#繰り返しパターンdo-while--キュードレイン)
- [並列実行パターン（Fan-out/Fan-in）](#並列実行パターンfan-outfan-in)
- [ゲートステートパターン（検証ゲート）](#ゲートステートパターン検証ゲート)
- [ReActアンカリング（思考-行動-観察）](#reactアンカリング思考-行動-観察)
- [自己検証フィールド（出力前チェックリスト）](#自己検証フィールド出力前チェックリスト)
- [マイルストーンアンカー（中間結果の明示的保存）](#マイルストーンアンカー中間結果の明示的保存)
- [Saga（補償トランザクション）パターン](#saga補償トランザクションパターン)
- [パターン選択ガイド](#パターン選択ガイド)

---

## 繰り返しパターン（do-while / キュードレイン）

### 一般名と対応関係

| 一般名 | 文脈 | 説明 |
|---|---|---|
| **do-while ループ**（後判定ループ） | プログラミング全般 | 処理を先に実行してから継続条件を判定する最も基本的な繰り返し |
| **Sequential Loop / Loop Activity** | BPMN 2.0 標準 | ワークフロー仕様における後判定繰り返しの標準名 |
| **Queue Drain Pattern**（キュードレイン） | 分散システム・キュー処理 | 処理対象を1件ずつ消費し、なくなるまで繰り返す。「処理→次があるか確認→なければ終了」がこれ |
| **ContinueAsNew Loop** | Temporal / Dapr Workflow | 長期実行ワークフローで履歴サイズ上限を避けながらループを継続する手法 |
| **Unfold / iterate-until** | 関数型プログラミング | 終了条件を満たすまで値を生成し続ける高階関数ベースのパターン |

ステートマシンで「処理後に次があるか問い合わせてなくなるまで繰り返す」フローは **Queue Drain Pattern** が最も正確な一般名。内部実装は **do-while ループ**（自己ループトランジション）で表現する。

---

### ① 基本 do-while ループ（カウンター制御）

処理を実行してから継続条件を確認する最小形。最大回数を超えたら強制終了する。

```yaml
context:
  iteration: 0
  max_iterations: 5

states:
  work:
    description: "繰り返し実行するステート"
    action_file: actions/work.md
    output_key: work_result

  done:
    description: "完了"
    action_file: actions/done.md
    terminal: true

transitions:
  - from: work
    to: work              # do-while の自己ループ
    condition: "最後の出力が CONTINUE で始まり、かつ {{iteration}} が {{max_iterations}} 未満"
    priority: 1
  - from: work
    to: done
    condition: "最後の出力が DONE で始まる、または {{iteration}} が {{max_iterations}} 以上"
    priority: 2
```

**`actions/work.md`:**

```markdown
## [work: 繰り返し処理を1回実行する]

現在の反復回数: {{iteration}} / {{max_iterations}}

[1回分の処理内容を記述]

処理後、以下のいずれかを最初の行に出力してください:
- `CONTINUE` — 処理は成功したが、まだ継続が必要
- `DONE` — 処理が完了し、繰り返しを終了してよい

その後に処理の詳細を記載してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

### ② Queue Drain Pattern（処理-継続確認ループ / consume-until-empty）

処理対象リスト・キュー・タスク群を1件ずつ消費し、なくなるまで繰り返す。  
**処理を完了してから「次があるかどうか」を確認する**のが do-while との共通点だが、  
Queue Drain は「残量の確認」がループ継続条件になる点が特徴。

```yaml
context:
  processed_items: "[]"   # 処理済みアイテムのJSON配列
  remaining_count: 0      # 残アイテム数（アクション内で更新）

states:
  check_queue:
    description: "キューに処理対象があるか確認する（初回 or 継続判定）"
    action_file: actions/check_queue.md
    output_key: queue_status

  process_one:
    description: "キューから1件取り出して処理する"
    action_file: actions/process_one.md
    output_key: process_result

  all_done:
    description: "全件処理完了"
    action_file: actions/all_done.md
    terminal: true

transitions:
  # 初回: キューに項目があれば処理開始
  - from: check_queue
    to: process_one
    condition: "queue_status が HAS_ITEMS で始まる"
    priority: 1
  - from: check_queue
    to: all_done
    condition: "queue_status が EMPTY で始まる"
    priority: 2

  # 1件処理後: 次があれば継続、なければ完了
  - from: process_one
    to: process_one       # キュードレインの自己ループ（次の1件を処理）
    condition: "process_result に MORE_ITEMS が含まれている"
    priority: 1
  - from: process_one
    to: all_done          # キュー枯渇 → ドレイン完了
    condition: "process_result に NO_MORE_ITEMS が含まれている"
    priority: 2
```

**`actions/check_queue.md`:**

```markdown
## [check_queue: 処理対象の有無を確認する]

以下の処理対象を確認し、未処理のアイテムがあるかどうかを調べてください:

[処理対象の取得・確認方法を記述]

**出力形式:**
- 処理対象が1件以上ある場合: `HAS_ITEMS` を最初の行に出力し、件数と対象リストを続ける
- 処理対象がない場合: `EMPTY` を最初の行に出力する

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

**`actions/process_one.md`:**

```markdown
## [process_one: キューから1件取り出して処理する]

前の確認結果: {{queue_status}}
これまでの処理済みアイテム: {{processed_items}}

未処理のアイテムを1件取り出して処理してください。

[1件の処理内容を記述]

処理後、残りのアイテムを確認し、以下の形式で出力してください:
- まだ未処理が残っている場合: `MORE_ITEMS` を最初の行に出力し、処理した内容と残り件数を続ける
- 全件処理完了の場合: `NO_MORE_ITEMS` を最初の行に出力し、処理サマリを続ける

**重要**: 今回処理したアイテムは「次への問い合わせ」で確認するのであって、
まだ残りのアイテムを先取りして処理してはいけません。1件ずつ処理してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

### ③ ユーザー問い合わせ型繰り返し（Interactive Queue Drain）

処理完了後にユーザー（または外部システム）に「次のタスクがあるか」を確認してから継続を判断するパターン。
Queue Drain の変形で、継続判定をLLMではなく外部入力に委ねる場合に使う。

```yaml
states:
  process:
    description: "現在のタスクを処理する"
    action_file: actions/process.md
    output_key: process_result

  ask_continue:
    description: "次のタスクがあるかユーザーに問い合わせる"
    action_file: actions/ask_continue.md
    output_key: continue_decision

  wrap_up:
    description: "全タスク完了のまとめ"
    action_file: actions/wrap_up.md
    terminal: true

transitions:
  - from: process
    to: ask_continue
    condition: "最後の出力が PROCESSED で始まる"
    priority: 1
  - from: ask_continue
    to: process           # 次のタスクがある → 処理ステートへ戻る
    condition: "continue_decision に NEXT_TASK が含まれている"
    priority: 1
  - from: ask_continue
    to: wrap_up           # タスクなし → 完了
    condition: "continue_decision に NO_MORE が含まれている"
    priority: 2
```

**`actions/ask_continue.md`:**

```markdown
## [ask_continue: 次のタスクがあるか確認する]

前のタスクの処理結果:
{{process_result}}

次に処理すべきタスクがあるかどうか確認してください:

[次のタスクの有無を確認する方法を記述。例: ユーザーへの問い合わせ、チケット一覧の参照、キューの確認 等]

**出力形式:**
- 次のタスクがある場合: `NEXT_TASK: [タスクの概要]` を最初の行に出力する
- タスクが全て終わった場合: `NO_MORE` を最初の行に出力する

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

---

### パターン選択基準

| 状況 | 選択するバリアント |
|---|---|
| 処理回数が予め決まっている | ① 基本 do-while（カウンター制御） |
| 処理対象リスト・キューを順番に消費する | ② Queue Drain Pattern |
| 処理後にユーザー/外部システムへ「次があるか」を問い合わせる | ③ ユーザー問い合わせ型 |

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
| 繰り返し処理（回数制御） | ① do-while 基本ループ |
| 処理対象リスト・キューを消費する | ② Queue Drain Pattern |
| 処理後に「次があるか」をユーザー/外部に問い合わせ | ③ ユーザー問い合わせ型繰り返し |
| 任意のステートからエラーへ飛ぶ | ワイルドカードトランジション |
