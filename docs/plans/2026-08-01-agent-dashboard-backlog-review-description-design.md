# Agent Dashboard バックログ計画レビュー記述設計

> 作成日: 2026-08-01
> 対象: `schemas/task.schema.json` / `tools/agent-project` / `backlog-planner` / `tools/agent-dashboard`

## 背景と課題

`agent-project` には、エージェントが charter をバックログへ分解し、`proposed` のタスクを人が
実行前にレビューする流れがすでにある。しかし、タスクの記述品質にばらつきがあり、タイトルと
受入基準だけでは「なぜ必要か」「何をどう変えるか」「どこまでが対象か」を判断しにくい。

`gitlab-idd` の利用者は、リクエスターが DesignDoc 形式の Issue を作り、人が内容をレビューして
進める流れに慣れている。この長所を Agent Dashboard に取り込む一方、DesignDoc 専用レコードや
承認工程を追加すると、人のレビュー回数と状態管理が増える。

本設計は agent-tools コンセプトの柱 2「人介在の最適化」、特に C3（機械で決められることを
人に聞かない）・C4（人へ聞くときは材料を揃える）・C5（人の判断を操作契約として記録する）に
効く。既存の計画レビュー 1 回に判断材料を集約し、新しいレビュー工程は設けない。

## 目的

- エージェントが生成したバックログを、人が実装着手前に短時間で理解できるようにする
- `gitlab-idd` の DesignDoc に近い読み順を、既存のタスク契約で実現する
- 人がレビューする回数と状態遷移を増やさない
- 承認された記述を、そのままワーカーの実行入力と検証入力に使う

## 非目標

- DesignDoc 専用ファイル、ネストした `design_doc` オブジェクト、専用ステータスの追加
- 全タスクへの代替案比較の強制
- GitLab Issue、`gitlab-idd` のロール、ラベル、コメントマーカーへの実行時依存
- 人の承認による機械検証の代替

## 採用設計

### 1. 既存フィールドを正規形として再利用する

タスクの説明は次の既存フィールドを正規形として使う。

| レビュー上の問い | task フィールド | 扱い |
|---|---|---|
| なぜ必要か、現状の何が問題か | `why` | 必須 |
| 何をどう変えるか、採用理由 | `desc` | 必須・複数行 |
| どこを変更してよいか | `scope` | 計画レビュー対象では必須 |
| 何をしないか | `out_of_scope` | 任意 |
| 何を守るか | `constraints` | 任意 |
| 何を満たせば完了か | `task_acceptance_criteria` | 必須・3〜7項目を目安 |
| どの順序で進めるか | `after` | 任意 |
| どの程度の規模か | `size` | 必須 |

`desc` は短い一文に畳まず、変更対象・作業方針・影響範囲を一項目一行で保持する。JSON では
文字列配列、Markdown では既存の複数行表現を使う。`why` と `desc` の責務は分け、価値と手段を
重複記述しない。

### 2. `risks` を一つだけ追加する

DesignDoc から取り込む新しい一次表現は `risks` のみとする。

```json
"risks": [
  "認証情報を画面やログへ露出させない",
  "一時的な通信失敗を権限不足と誤判定しない"
]
```

- 型は `string | string[]`
- JSON の新規書き込みは配列を使う
- Markdown では同じキーの複数行を許容する
- 計画レビュー対象ではキーの存在を必須とし、該当しない場合は `"なし"` を明記する
- 実行時は `why` や `constraints` と同様にワーカーの要求文へ注入する
- 完了条件にはしない。done の根拠は引き続き verification receipt とする

`alternatives` は追加しない。全バックログで代替案を要求すると、小さな作業にも比較文が増え、
レビュー負荷が上がる。採用理由が判断に必要な場合は `desc` に記録する。

### 3. 計画レビュー票の表示順を固定する

Agent Dashboard の `plan-review` は次の順で一枚のレビュー票を表示する。

1. なぜ必要か — `why`
2. 何をどう変えるか — `desc`
3. 対象／対象外 — `scope` / `out_of_scope`
4. リスク — `risks`
5. 受入基準 — `task_acceptance_criteria`
6. 規模・依存関係 — `size` / `after`
7. 制約・参考情報 — `constraints` / `hints` / `refs`

空の任意項目は表示しない。必須項目が欠けている場合だけ、現在と同じく不足を明示して修正を促す。
内部キー名や生の Markdown は通常表示せず、人が判断する言葉へラベルを変換する。

### 4. レビュー状態は増やさない

状態遷移は既存のままとする。

```text
charter
  -> backlog-planner がタスク案を生成
  -> proposed（既存の計画レビュー）
  -> 人が修正・承認
  -> ready
  -> doing / offloaded
  -> verification
  -> done または review / blocked
```

DesignDoc の事前承認、設計承認済み状態、二段階レビューは追加しない。複数タスクは同じ計画レビュー
セッションで読み、個別修正を可能にしたまま一括承認できることを目標とする。一括承認は各タスクへの
既存 `approve` 操作をまとめて投函する UI であり、新しい状態遷移ではない。

## gitlab-idd との対応

| gitlab-idd DesignDoc | Agent Dashboard backlog |
|---|---|
| 背景・目的、問題定義 | `why` |
| 提案ソリューション | `desc` |
| 代替案 | 原則省略。必要時のみ `desc` に採用理由 |
| リスクと対策 | `risks` |
| 実装スコープ | `scope` / `out_of_scope` |
| 技術制約 | `constraints` |
| 受け入れ条件 | `task_acceptance_criteria` |
| 依存イシュー | `after` |
| 参考情報 | `hints` / `refs` |

テンプレートの知見だけを取り込み、GitLab 固有の保存形式やワークフローには結合しない。

## データフローと責務

### backlog-planner

- `why` / 複数行 `desc` / `scope` / `risks` / `task_acceptance_criteria` / `size` を出力する
- repo-map を根拠に `desc` と `scope` を記述する
- リスクが無い場合も `risks: ["なし"]` を返す
- 既存タスクと墓標を読み、同じ意図を再提案しない

### agent-project

- 出力を task spec へ正規化し、複数行を失わず保存する
- 計画レビュー対象の必須項目を決定的に検査する
- 欠落時は一度だけプランナーへ再要求する
- 再要求後も不足するタスクは捨てず、`proposed` と不足理由を人へ提示する
- 承認された全記述を act prompt に注入する

### agent-dashboard

- 既存の `plan-review` に固定順の説明を表示する
- `plan-review` では説明を初期展開し、承認・差し戻し操作より先に表示する
- 配列・旧文字列・Markdown の複数キーを同じ一覧へ正規化する
- 人が各項目を修正できるよう、既存 `revise` 操作契約を使う
- 一括承認は未対応画面に表示中の計画だけを対象とし、確認時に ID とタイトルを列挙する
- 一括承認でもタスクごとの決定記録を残し、処理中は操作を無効化する
- タスク追加では計画レビュー必須項目と `size` を初期表示する
- 既存の読み取り専用 `task-guide` AI 補完で空の必須項目だけを埋め、人が確認してから追加する

### backlog-verifier

- `task_acceptance_criteria` を検証し、証跡付き receipt を生成する既存責務を維持する
- `risks` は判定基準に自動変換しない。必要な安全条件は planner が受入基準にも明記する

## 後方互換性

- `additionalProperties: true` のため古い reader は `risks` を無視して動作できる
- `risks` のない既存タスクは読み取り可能とし、再レビューや移行を強制しない
- `acceptance` / `accept` は既存どおり読み取り互換を維持し、新規生成だけ
  `task_acceptance_criteria` を使う
- 既存の `desc` 文字列も一項目の配列として表示する
- `proposed -> ready` などの状態遷移、needs 投影、決定記録の形式は変更しない

## エラーハンドリング

| 状況 | 対応 |
|---|---|
| planner が必須項目を欠落 | 一度だけ再要求し、なお不足なら `proposed` で人へ出す |
| `risks` が空 | 不足項目として表示。「なし」と空欄を区別する |
| 配列でない旧値 | 一項目として正規化して表示・注入する |
| 一括承認の一部が失敗 | 成功したタスクは戻さず、失敗したタスクIDと理由を表示して再実行可能にする |
| 不明な追加キー | 現行契約どおり保持し、通常 UI では詳細情報として扱う |

## テスト方針

最小の回帰チェックを責務ごとに置く。

1. task schema が `risks: string | string[]` を受理する
2. backlog-planner のプロンプトが必須項目と `risks: ["なし"]` の規則を含む
3. agent-project が `desc` / `scope` / `risks` の配列を失わず Markdown へ保存・再読込する
4. 必須項目欠落が再要求され、二度目も不足なら `proposed` へ残る
5. plan-review が固定順で表示され、旧文字列形式も読める
6. 承認後の `ready` 化と verification receipt に既存回帰がない
7. 一括承認の部分失敗がタスク単位で表示される

## 実装計画

1. `schemas/task.schema.json` と `tools/agent-project/backlog.md.example` に `risks` を追加する
2. `backlog-planner` の出力契約とプロンプトを、複数行 `desc`・必須 `scope`・`risks` に更新する
3. agent-project の planner 出力正規化、必須項目ゲート、prompt 注入を更新する
4. Agent Dashboard の task parser と計画レビュー票を固定順・複数行対応へ更新する
5. 既存 revise 契約に `risks` を通し、必要なら一括承認 UI を既存 approve の束として追加する
6. スキーマ、planner、保存往復、UI、状態遷移の回帰テストを追加する
7. 正典である `docs/designs/agent-project-design.md` と `docs/designs/agent-dashboard-design.md` を更新する

## 代替案とトレードオフ

| 案 | 実装コスト | リスク | 保守性 | 拡張性 | 学習コスト | 推奨度 |
|---|---|---|---|---|---|---|
| 既存項目を再利用し `risks` のみ追加 | 低 | 低 | 高 | 中 | 低 | ★★★ |
| `problem` / `solution` / `alternatives` / `risks` を追加 | 中 | 中 | 中 | 高 | 中 | ★★☆ |
| ネストした `design_doc` を新設 | 高 | 高 | 低 | 高 | 高 | ★☆☆ |

採用案は既存の実行契約とレビュー契約を共用でき、説明の重複と移行コストが最も少ない。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-01 |
| 決定者 | チーム |
| 採用案 | 既存バックログ項目を再利用し、`risks` のみ追加する |
| 却下案 | DesignDoc 専用レコード（レビュー工程と状態が増えるため）、複数の設計フィールド追加（既存項目と重複するため） |
| 主な理由 | 既存の計画レビュー1回に判断材料を揃え、レビュー回数を増やさず理解しやすくできる |
| トレードオフ | 代替案を独立フィールドとして検索・比較できない。必要時は `desc` に採用理由を書く |
| 再評価条件 | タスク単位で複数の設計案を比較・承認する需要が継続的に発生した場合 |
