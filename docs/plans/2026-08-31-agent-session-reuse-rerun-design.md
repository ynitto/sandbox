# 過去セッション・run の流用実行設計 — 編集付き再実行・テンプレート化・map / 反復

> 作成 2026-08-31 ／ 対象: `tools/agent-dashboard`（adhoc-flow / agent-audit / routines）・
> `tools/agent-flow`（inbox 契約への追記のみ）・`schemas/agent-workflow.schema.json`
>
> 効く柱・原則: **柱2×柱3 / C4・C7・C8** — 過去の run・素の CLI セッションを種に、
> AI 下書き＋人の確定で入力を編集して再実行し、繰り返す流用は保存形テンプレートと
> 既存の反復機構（map-reduce / バッチ投函 / routine）へ載せる。transcript はノード外へ出さない。

## 背景

過去のエージェント実行を「参考にし、入力を変えて、もう一度実行する」ための一貫した入口がない。
現状できるのは同一入力の完全複製（adhoc-flow の再実行）とタスクの積み直し（agent-project の
`revise`）だけで、次の 3 つが欠けている。

1. 過去 run の入力（要求文・plan・workspace）を**編集して**再投入する経路
2. agent-flow run を持たない**素の CLI セッション**（対話利用・agent-loop / cowork）を種にする経路
3. その流用処理を **map（n 件の入力へ展開）や iteration（定期・収束まで）で繰り返し使う**経路

UI は agent-dashboard が担う。成果物は複数リポジトリにまたがることを許容するが、
既存の設計思想（1 run = 1 workspace、dashboard は読むのはファイル・書くのは契約の投函だけ、
transcript はノード外へ出さない、すべての反復は有界）は維持する。

## 調査結果（既存の足場）

- **入力の永続記録は既にある。** agent-flow の `inbox/<run-id>.json` は run 完了後も残り、
  `request` / `plan` / `workspace` / `references` / `execution_overrides` /
  `granularity` / `split_policy` を保持する（spec §3.1）。バスから消えた run も dashboard の
  `flow-archive/<run-id>.json` と世代交代の墓標 `runs/<id>/inherited/` に読み取り専用の写しが残る。
- **同一入力の再実行は実装済み。** `src/features/adhoc-flow/main/adhoc.js` の `resubmit()` が
  旧 inbox 記録を新 id で複製し、系譜キー `root_run_id` / `previous_run_id` を付ける。
  ただし**入力を編集するパラメータを持たない**。
- **`inherit_from` は世代交代＝リトライの機構**で、先行 run を墓標化して削除する
  （agent-flow-design §6.3）。agent-project は「plan が変わったら inherit しない」
  （`agent_project/flow.py` の `_plan_changed_since_last_run`）を既に採っている。
- **素の CLI セッションの本文は agent-audit の transcripts だけが持つ。**
  `with_transcripts` 有効時のみ `~/.agents/audit/transcripts/<cli>/<sid>.jsonl`
  （User / Assistant の整形済み本文）が残る。設計不変条件「transcripts はノード外へ出さない」、
  保持既定 30 日。既定の `records/` はメタデータのみで本文を含まない。
- **変数機構は 2 か所に前例がある。** ワークフロー保存形の goal は投入時に `{{request}}` を
  置換する（`renderer/features/adhoc-flow.js`）。定常業務は `{{key}}` / `{{input}}` /
  `{{context.KEY}}` の検出と入力ダイアログを持つ
  （[2026-08-09 定常業務パラメータダイアログ設計](./2026-08-09-agent-dashboard-routine-parameters-dialog-design.md)）。
- **run 内の反復は既に有界機構がある。** `split → map×N → reduce`（データ駆動 fan-out・
  `--max-fanout`）と `loop-until-done`（`max_iterations`）（agent-flow-spec §のパターン表）。
- **既存の非目標との関係。**
  [2026-08-12 ワークフロー実行管理設計](./2026-08-12-agent-dashboard-workflow-run-management-design.md)
  の非目標「名前付き実行プロファイルを作らない」は execution_overrides（agent / model 指定）の
  保存を禁じたもの。**作業内容**の保存はワークフローライブラリ
  （`schemas/agent-workflow.schema.json` の保存形）の本来の用途であり、衝突しない。

## 検討した案

| アプローチ | 実装コスト | リスク | 保守性 | 拡張性 | 学習コスト | 推奨度 |
|---|---|---|---|---|---|---|
| A. 既存契約の拡張（resubmit 編集・保存形テンプレート・バッチ投函） | 低〜中 | 低 | 高 | 高 | 低 | ★★★ |
| B. transcript を直接再実行の入力にする「セッションリプレイ」基盤 | 中 | 高（不変条件違反・秘密漏洩） | 低 | 中 | 中 | ★☆☆ |
| C. 新しい実行テンプレート基盤（独自スキーマ・専用ストア）を並設 | 高 | 中 | 低 | 高 | 高 | ★☆☆ |

案 A を採用する。再利用単位は既存のワークフロー保存形へ昇格させ、実行・反復は既存機構
（adhoc inbox 投函・map-reduce・routine）に載せる。新しいエンジン・デーモン・ストアは作らない。

## 採用設計

### 1. 編集付き再実行（fork）

- `resubmit(config, runId)` を `resubmit(config, runId, edits)` へ拡張する。`edits` で
  `request` / `plan` / `workspace` / `references` / `execution_overrides` /
  `granularity` / `split_policy` の上書きを許し、それ以外は従来どおり旧 inbox 記録を引き継ぐ。
- 投函する inbox 記録に `edited_fields: [...]`（任意キー）を追記し、「何を変えた再実行か」を
  証跡として残す（C8）。agent-flow spec §3.1 への追記は任意キーの追加のみで互換。
- **`inherit_from` は使わない。** 入力を編集した再実行は世代交代（先行 run の墓標化・削除）では
  なく**分岐（fork）**であり、旧 run は参照として残す。系譜は既存の
  `root_run_id` / `previous_run_id` だけで繋ぐ。これは agent-project の
  「plan が変わったら inherit しない」判断、agent-flow 設計 §6.3 の「新世代の request が正」と
  同じ哲学である。入力を変えない再実行は従来の verbatim 複製のまま。
- UI: run 詳細画面（[2026-08-14 実行 UX 再設計](./2026-08-14-agent-dashboard-workflow-run-ux-redesign-design.md)）の
  「再実行」を「同じ入力で再実行」「入力を編集して再実行」の二択にする。後者は既存の新規実行
  フォームを旧 inbox 記録の値でプリフィルして開き、確定時に投函する。系譜表示は既存の
  `root_run_id` グルーピングを流用する。
- agent-project 管理下のタスク（`req-…-r<n>-v<rev>`）は対象外。そちらの「入力を変えて積み直す」は
  既存の `revise` コマンドが正であり、経路を 2 つにしない（C7）。

### 2. 素の CLI セッションの流用 — 蒸留してテンプレートにする

transcript を直接再実行の入力にしない。**セッション → 再利用可能な要求文 / ワークフローへの
蒸留**を挟み、蒸留物を一級市民にする。

- 入口: dashboard の利用状況（agent-audit）領域のセッション一覧へ「このセッションを種に」を
  足す。ローカルの transcript を読み、**AI が要求文またはワークフロー下書きを生成 → 人が編集・
  確定**する（C4: AI は下書きまで、確定は人のボタン）。本文は agent-audit の既存
  cleaning / scrub を通してから下書きの材料にする。
- 確定した蒸留物は、(a) そのまま 1 回だけ adhoc 投函する、または (b) ワークフロー保存形として
  `~/.agents/workflows/` か登録フォルダの `.agents/workflows/` へ保存する。秘密・ローカルパスを
  除いた蒸留物は共有リポジトリへ置いてよい（C1）。**transcript 本文は inbox・state repo・
  保存形のどこにも書かない**（agent-audit 不変条件の厳守）。
- **蒸留物が正本、transcript は消えてよい。** 30 日の GC で transcript が消えても流用資産は残る。
- agent-audit は読み手のまま（C7）。transcript を読むのは dashboard、書き先は adhoc inbox への
  投函とワークフローライブラリだけで、agent-audit に実行の役割を足さない。
- `with_transcripts` が無効のノードでは、この入口はメタデータ表示＋空フォームへ縮退する。

### 3. 系譜（provenance）

保存形ワークフローへ任意フィールド `source` を追加する。値は蒸留・昇格の複製元:
`session/<agent_cli>/<session_id>` ／ `run/<run-id>` ／ 手書きなら省略。
作業ルール（nodeMethod）の `source: "methods/<id>@<hash>"` と同じ「複製元表記」の流儀に揃える。
これで「どのセッション・run から生まれ、どのタスクへ適用され、何の検証に通ったか」を
run 側の系譜キーとあわせて追跡できる（C8）。

### 4. 変数 — `{{key}}` を保存形テンプレートへも許す

- 保存形の goal / request 内の `{{key}}` を入力パラメータとして扱う。検出ロジックと入力
  ダイアログは**定常業務側の既存実装を共有**し、2 実装にしない（C7）。
- 予約語（`{{request}}`・statemachine の組み込み変数）は従来の意味を維持し、入力扱いしない。
- 入力型は定常業務設計と同じく文字列だけとする。

### 5. 繰り返し流用 — 反復の 4 形はすべて既存機構へ振り分ける

| 反復の形 | 使う機構 | 新規に要るもの |
|---|---|---|
| map: n 件を同一リポジトリで展開し結果を統合 | run 内の `split → map×N → reduce`（`--max-fanout` で有界）。ユーザー定義フローで split ノードへ入力リスト（JSON 配列）を渡す | テンプレートのパラメータを split 入力へ渡す配線のみ |
| map: n 件が独立実行・**リポジトリをまたぐ** | **バッチ投函**（下記） | 本設計で唯一の実質新機能 |
| iteration: 定期反復 | 定常業務（routines / agent-loop）へテンプレートを登録。`{{key}}` ダイアログは既存 | 蒸留物を routine に登録する導線のみ |
| iteration: 収束まで反復 | run 内は `loop-until-done`（`max_iterations` で有界）。run をまたぐ手動反復は §1 の fork チェーン。目標駆動の自動反復は agent-project（charter） | なし |

**バッチ投函**: パラメータ行（表形式・行ごとに `{{key}}` の値の組）× テンプレート →
n 本の adhoc run を一括投函する。

- 各 run は自分の `workspace` を持つ（**1 run = 1 workspace は維持**）。行ごとに workspace を
  変えられるため、成果物のリポジトリまたぎはここで満たす。読み取りの跨ぎは既存の `references`。
- inbox 記録へ任意キー `batch_id` を追記し、`root_run_id` とあわせて一覧を束ねて表示する。
- **投函前に「n 件 × 概算予算」の確認と件数上限を必ず置く**（C1・C7: 消費は宣言予算の内側、
  処理は必ず止まる）。実行は従来どおり常駐体が node-budget の枠内で消化し、dashboard は
  投函するだけで実行しない。
- 自ノードが担当宣言していないリポジトリ行は、agent-board の委譲（`repos.schema.json` の
  担当宣言を持つノードへの公示）へ流すことを許す。

## データ契約の変更点（すべて任意キーの追加・互換）

| 契約 | 変更 |
|---|---|
| agent-flow inbox 記録（spec §3.1） | `edited_fields: string[]`・`batch_id: string` を任意キーとして追記（`root_run_id` / `previous_run_id` は既存の dashboard 慣行を spec に明文化） |
| `schemas/agent-workflow.schema.json` 保存形 | `source: string`（複製元表記）と、goal / request 内 `{{key}}` の意味（入力パラメータ・予約語の除外）を description に明記 |
| agent-audit | 変更なし（transcripts の読み取りのみ。不変条件・保持期限は現行のまま） |

## UI 変更点（agent-dashboard）

1. run 詳細画面: 「再実行」を二択化（同じ入力 / 入力を編集）。編集側は新規実行フォームの
   プリフィル再利用。
2. 利用状況（agent-audit）領域: セッション一覧へ「このセッションを種に」→ AI 下書き →
   編集・確定 → 投函またはライブラリ保存。
3. ワークフロー領域: 保存形の投入時に `{{key}}` を検出して入力ダイアログを出す
   （定常業務の実装を共有）。「バッチ投函」でパラメータ表を受け、確認（件数・概算予算）後に
   一括投函。一覧は `batch_id` で束ねる。

## 非目標

- 名前付き実行プロファイル（execution_overrides の保存・再利用）。2026-08-12 設計の非目標を維持。
- transcript のノード外への共有・保存形への本文埋め込み。どの理由でも行わない。
- セッションの逐語リプレイ（会話履歴をそのまま再送する実行）。
- 1 run で複数 workspace へ書く拡張。またぎは行分割（バッチ投函）と委譲で満たす。
- 中央スケジューラ・新デーモン・新ストアの追加。
- agent-project タスクの流用経路の新設（既存 `revise` が正）。

## 原則チェック（コンセプト正典 §8.4）

- C5: done の確定は従来どおり機械検証のみ。流用・バッチ投函は投入経路であり verify を迂回しない。
- C3・C4: 蒸留は AI 下書き＋人の確定。バッチ確認は件数・概算予算の材料を揃えて 1 回で決めさせる。
- C6: 種は inbox 記録・保存形・（ローカル限定の）transcript。決着は従来の状態から導き、在席に依存しない。
- C1・C7: バッチは件数上限と予算確認つき。実行は node-budget の内側で常駐体が消化。
  検出ロジック・resubmit は 1 実装を共有し、書き手は投函者のみ。
- C1（配布境界）: 共有してよいのは scrub 済みの蒸留物だけ。transcript・ローカルパス・秘密は配らない。
- C8: `source` と `edited_fields` / `root_run_id` / `batch_id` で複製元→適用→検証の系譜を追える。
- C9・C10: 実行時のモデル選定・降格は既存機構のまま（本設計は触らない）。

## 段階導入

1. **P1 — 編集付き再実行**: `resubmit(edits)` ＋ IPC ＋ 再実行二択 UI ＋ `edited_fields`。
2. **P2 — テンプレート化と変数**: セッション / run → 保存形への蒸留入口、`source`、
   `{{key}}` 検出の共有、投入時ダイアログ。
3. **P3 — バッチ投函**: パラメータ表・件数上限・予算確認・`batch_id` 束ね表示・委譲行の board 流し。
4. P2 以降で routine 登録導線と run 内 map（split への配線）を接続する。
