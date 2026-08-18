# ワークフロー機能の改善 — 実装記録（提案 P1〜P6）

対象の提案書: [`2026-08-15-workflow-feature-improvement-proposals.md`](./2026-08-15-workflow-feature-improvement-proposals.md)。
本書は、その 6 提案をどのレイヤーへどう落としたかと、文言でしか守られていない契約が残っていないか
（＝各契約の強制レイヤー）を記録する。

## 目的

`af/adhoc-20260815-235229-7532` の後追い修正を生んだ 4 つの構造要因（水平分割・完了条件・表現の契約・
強制レイヤーの取り逃し）に対して、次の run から効く仕組みを入れる。個別バグの再修正は対象外。

## 変更対象

| 提案 | 変更対象 | 強制レイヤー（実行時に効く場所） |
|------|---------|--------------------------------|
| P1 統合検証の標準装備 | `agent-dashboard` renderer の雛形生成（`templateWorkflow` / `withIntegrationVerify`）、run 表示（`integrationVerifyPresentation`） | 雛形が持つ `kind: verify` + `continuation: retry` のノード。plan 生成が `evaluate: true` を立て、agent-flow の評価役が verify の fail を作り直しへ回す。完了表示は run の終端検証ノードの結果で決まる（文言ではない） |
| P1 テスト追加ノードの証跡 | `methods/test-green-evidence.json`、ノードの `surface: test` | plan 生成（`planFromWorkflow`）が goal へルール本文を複製する。カタログから引けなければ run を起動しない（フェイルクローズ） |
| P2 分割単位 | `agent-flow` の `split_policy` / `split_policy_directive` | planner プロンプトへ必ず後置する。既定 `behavior`、ファイル水平分割は `--split-policy file`（設定 `split_policy`）の明示オプト |
| P3 UI 一貫性の作業ルール | `methods/ui-consistency.json`、ノードの `surface: ui` | P1 と同じ plan 生成の複製経路。画面（編集キャンバス）は面の選択肢を main の `NODE_SURFACES` から受け取るだけ |
| P4 レビュー観点 | `agent-flow` の `REVIEW_LENSES` / `review_lens_directive`、`orchestrate` の `evaluate` イベント | 評価役プロンプトへ一律後置（スキル経路・組み込み経路の両方）。ラウンドの記録は bus のイベント（成果ゼロでも必ず 1 件） |
| P5 強制レイヤー | `agent-dashboard` の `tools/agent-dashboard/src/base/main/design-contract.js`、同梱設計フローの goal | 設計成果の検査。`変更対象` 節に強制レイヤーが無い成果は `implementation-ready` にしない（設計セッションの取り込みと作業準備の handoff の両方で同じ 1 実装を通る） |
| P6 公開後の CI（読み手） | `publicationPresentation` / `ciPresentation` と実行の助言 | 公開レコード（`publication` / `delivery`）に載った `ci` を読むだけ。dashboard から CI へ問い合わせはしない |
| P6 公開後の CI（書き手） | `agent-flow` の `ci.py`（`ci_status_command` / `ci_wait_seconds` / `ci_poll_seconds`） | run の終端で公開済み commit の CI 状態を宣言コマンドへ問い合わせ、結果ノードの `publication.ci` と `final.ci` へ書き戻す。読めない・壊れた出力は `unknown`（緑へ倒さない） |
| P1 run の完了条件（エンジン） | `agent-flow` の `terminal_verification` / `_finalize_run` | 終端 verify の判定（`data.ok` → `_normalize_verify` の 1 実装）で run を終端する。赤なら `failed` + `[verification]` タグ付き理由 + `final.verification` の記録 |

### 分割単位（P2）の既定

- `behavior`（既定）… 利用者から見える 1 つの振る舞いを 1 ノードが縦に持つ。UI を持つ振る舞いは
  マークアップ・スタイル・呼び出し側（同じ用途の UI が複数画面に出るならその全画面）を 1 ノードに含める。
  複数ノードが同じ用途の UI を要するときは、共有部品を切り出すノードを先に置いて後続が消費する。
- `file`… ファイル境界の水平分割。衝突回避が要る大規模変更のための明示オプションで、
  裂けたノードには「揃えるべき点と対応ノード id」を goal に書かせる。

### ノードが作るもの（surface）

`ui` / `test` の 2 値。plan 生成が対応する作業ルール（`methods/ui-consistency.json` /
`methods/test-green-evidence.json`）の本文をノードの goal へ複製する。**複製**なので、後からカタログを
編集しても実行済み・保存済みの run の振る舞いは変わらない（既存の手法スナップショットと同じ規約）。

### 積み残しの解消（第 2 段）

第 1 段では表示側だけを変え、次の 2 つを残していた。第 2 段で実装した。

- **P6 の CI 結果の書き手**（`tools/agent-flow/agent_flow/ci.py`）。CI ごとのクライアントは持たず、
  利用者が宣言したコマンドの JSON 出力を正典にする（統一 verify の固定コマンドと同じ作法）。
  既定は off で、宣言が無い run の振る舞いは 1 バイトも変わらない。状態は
  passed / failed / running / unknown の 4 値で、**読めないときは unknown**——黙って緑へ倒すと
  赤い CI の成果が「完了」として下流へ流れるため。`ci_wait_seconds` で終端まで有界に待てる
  （既定 0 = 1 回だけ問い合わせる。上限 1800 秒 = orchestrator の lease 内）。
- **P1 の run 状態そのもの**（`terminal_verification` / `_finalize_run`）。終端 verify の判定を
  run の完了条件にし、赤なら `failed` で終端する。判定の読み方は `_normalize_verify` の 1 実装に
  委ね、orchestrator 側で本文を再解釈しない（"no failures" を赤と読むような二重解釈を作らない）。
  終端に verify が無い run の見え方は変わらない。dashboard は判定を読むだけになり、成果テキストからの
  読み直しは記録の無い旧 run に限る。

#### この 2 つで変わる運用

- 終端の検証が赤い run は `agent-flow run` の終了コードが 1 になり、`meta.failure_reason` に
  `[verification]` タグが付く。agent-project / 板の消費者は既存の失敗トリアージでそのまま拾える。
- CI の取り込みを宣言していない環境では CI 記録は作られず、画面は従来どおり「CI 記録なし」と出す
  （赤とは区別する）。

## 受入基準

- [x] 実装フローの雛形（標準パターン・作業ルール雛形）の終端に、再検証つきの統合検証が既定で付く。
      分割（split）が終端のフローと設計フローには付かない。
- [x] 終端の統合検証が赤のまま終端した run は「完了」ではなく「要対応」として表示される。
- [x] planner プロンプトに分割単位の指示が必ず入り、既定は振る舞い単位。ファイル単位は明示指定でだけ選べる。
- [x] 評価役プロンプトに 3 観点（二重実装・画面間の表現差異・文言量）が入り、成果ゼロのラウンドも
      run 履歴へ観点と所見が残る。
- [x] `surface` を宣言したノードの goal に、対応する作業ルールが複製される。ルールが引けなければ起動しない。
- [x] 設計成果は必須4節に加えて「変更対象の強制レイヤー」が無いと実装へ渡せない。設計セッションと
      作業準備が同じ判定を通る。
- [x] 公開レコードに CI 結果があれば公開状態と同じ場所に出し、赤なら要対応にする。
- [x] 取り込みを宣言した run は、公開済み commit の CI 状態を公開レコードへ書き戻す。読めない
      結果は unknown で記録し、緑にはしない。宣言が無い run では何も記録しない。
- [x] 終端の検証が赤い run は agent-flow 自身が failed で終端し、判定を `final.verification` へ残す。
      終端に verify が無い run の終端条件は変わらない。

## 検証方法

```
cd tools/agent-dashboard && npm test          # 雛形・完了条件・作業ルール・設計契約・CI 表示
cd tools/agent-flow && python3 -m unittest discover -s tests   # 分割単位・レビュー観点・評価イベント・CI 取り込み・完了条件
python3 -m unittest discover -s tools/agent-loop/test          # 同梱カタログの golden
```

## 第 3 段 — カスタマイズ口を既存の 1 本（作業ルール）へ寄せる

第 1・2 段は、改善案を実装するときに新しいカスタマイズ口を 4 つ足していた（ノードの面 `surface`、
統合検証の内容、設計成果の契約、agent-flow の設定 2 つ）。第 3 段では、それらを**既に在る仕組み**
——手法カタログ（作業ルール）と、実行時に `when` で注入する agentcore の 1 実装——へ寄せ、
足した口をすべて外した。

### なぜ寄せたか

- **ワークフローは作業フローの型**であって、成果物に特化しない。ノード定義へ「何を作るか」
  （`surface: ui` など）を持たせると、その型が特定の作り方へ縛られる。加えて、実行時に増える
  ノード（分類の後段・分割の後段・評価役が足す作り直し）は定義に書けないので、同じ仕事でも
  ルールが付くノードと付かないノードが混ざる。**何を作るかは実行時にしか決まらない。**
- 実行時に条件で作業ルールを足す仕組みは既にある（`agentcore.methods.select` が
  engine / workload / purpose / role / agent_cli / model / tier / cost で照合）。設定ファイルや
  ノード定義に別口を作ると、同じ目的の宣言が 2 箇所に散る。
- 統合検証（verify + `continuation: retry`）とその再作業は**改善前から在る仕組み**。P1 が足したのは
  「雛形の終端へ自動で 1 つ置く（画面）」と「終端検証の緑を run の完了条件にする（エンジン）」の
  2 つで、前者は画面が成果物特化の工程を勝手に足す設計だった。

### 何を外し、何で代替したか

| 外したもの | 元の課題 | 代わりの対策 |
|-----------|---------|-------------|
| ノードの面 `surface`（定義・正規化・digest・編集 UI・plan 生成の複製） | UI の二重実装／表現差異（P3）、テスト未実行のまま提出（P1） | 作業ルール `ui-consistency` / `test-green-evidence` を **実行時に** worker ロールへ注入する（手法ピッカーで選ぶか、tuning で常時有効にする）。plan 生成が定義から複製しないので、実行時に増えたノードにも同じルールが効く |
| 統合検証の自動付与（renderer の固定ノード）と、その内容のカタログ解決＋IPC 配線 | 「全体が緑」を完了条件にする工程が無かった（P1） | ① 完了条件は agent-flow の `terminal_verification`（第 2 段・エンジン側で強制）。② 検証工程を置くのはフローを作る人／planner。③ 検証のやり方は作業ルール `integration-verify`（`when.roles: [verify]`）が実行時に verify ノードへ足す |
| フロー定義の設計契約 `contract`（宣言・digest・snapshot） | 文言でしか守られていない契約（P5） | 設計書の書式を手法 `design-document-format` として定義。指示（`fragments`）と、実装へ渡す前に数える構造（`format`）が同じ 1 ファイルに並ぶ。リポジトリの `.agents/methods/` に同 id を置けば書式ごと差し替わる |
| agent-flow の設定 `split_policies` / `review_lenses` | 分割単位・レビュー観点をプロジェクトごとに変えたい | プロンプトへ足す文言は作業ルールの仕事。`when.roles: [planner]` / `[evaluator]` を宣言したルールが、実行時にそれぞれのプロンプトへ足される。設定キーは増やさない |

### 残した仕組み（エンジン側の強制）

- `terminal_verification` / `_finalize_run`（agent-flow）… 終端 verify が赤なら run を failed で終端する。
  文言ではなく実行結果で効く。
- `split_policy`（`behavior` / `file`）… 既存の粒度（`granularity`）と同じ流儀の 2 値。名前を増やす口は外した。
- `REVIEW_LENSES`（同梱 3 観点）… 評価役プロンプトへ一律後置。構成を変えたいときは作業ルールで足す。
- 設計書の書式を数える 1 実装（`design-contract.js`）… 書式そのものは持たず、カタログの宣言を読む。
  引けないときは「書式が無い」を不足として返す（黙って全部通さない）。

### 設計書の書式（`methods/design-document-format.json`）

汎用の既定として、必須節（目的・変更対象・受入基準・検証方法）と、節内の必須項目（変更対象の
強制レイヤー）を宣言する。`fragments` は設計 run の終端 goal へ複製される指示、`format` はゲートが
数える構造で、**同じ 1 ファイルに並べる**——指示と判定が別々に育つと「指示どおり書いたのに弾かれる」
が起きるため。壊れた `format` 宣言は「書式が無い」と同じ扱いにする（直したつもりの書式で走り続けない）。

### 検証の位置

設計成果の中身は受け入れ時（`completeDesign` / dashboard の設計取り込み）と、実装へ渡す直前
（`canHandoff` / `recordHandoff`）の両方で数える。保存済みの状態ファイルは手で編集も破損もするので、
phase だけを信じて不完全な設計書を実装 run へ流さない。

## 第 4 段 — 手法カタログのモデルを分ける

第 3 段でカスタマイズ口を作業ルール 1 本へ寄せた結果、カタログ 25 件の中に性質の違うものが
同居していることが表に出た。「成果物によらない汎用ルール」と「成果物ごとの専用ルール」という
分け方が最初に浮かぶが、実際に区別を生んでいるのはそこではない。

### 何が実際に違ったか

- **選択条件を実行時に機械で判定できるか**。既存の `when`（engine / workload / purpose / role /
  agent_cli / model / tier / cost）はすべて実行時に分かる事実なので、それだけで決まるルールは
  自動適用できる。一方「このノードが画面を触るか」は実行時にも判定できない。
- **指示か契約か**。`design-document-format` だけは、プロンプトへ足す指示に加えて
  **実装へ渡す前に機械で数える構造**を持つ。他の 24 件には無い。

### 分けたモデル

| モデル | 宣言 | 選択条件 | 決める人・時 |
|--------|------|---------|-------------|
| 作業ルール（自動適用） | `kind: rule`（既定）/ `selection: auto`（既定） | `when` の実行条件だけで決まる | エンジンが実行時に自動 |
| 作業ルール（工程ごと） | `selection: per-task` | 機械判定できない「その工程への指示」 | 人が工程ごとに（将来は planner） |
| 成果物の契約 | `kind: contract` | 成果物の種類で 1 つに決まる | 仕事の種類で自動。ON/OFF しない |

無指定は `rule` / `auto` として読む（既存定義の互換）。同梱の分類は、契約が
`design-document-format`、工程ごとが `integration-verify`（終端でパッケージ全体を回す手順は
中間の verify ノードへ一律に付けるものではない）、残り 23 件が自動適用。

### 既定 ON

`ui-consistency` と `test-green-evidence` を同梱カタログで `enabled: true` にした。どちらも文面が
**自己条件づけ**（「画面を変更する前に…」「テストを追加・変更したら…」）なので、触らない工程では
何も足さない。改善提案の元になった 2 つの失敗（同用途 UI の食い違い・一度も実行していないテストの
提出）へ既定で効かせるための選択で、端末設定（tuning）の宣言があればそちらが常に優先する。

実効化は run 専用 tuning の複製で行う。`adhoc.submit` が「既定 ON ＋ 利用者が有効化したもの ＋
プリセットが名指ししたもの」を run 単位で複製するので、走り出した run の振る舞いは後からカタログや
端末設定を変えても動かない。A/B 試行（trials）は端末設定の宣言をそのまま運ぶ（複製で落とすと、
宣言した試行が dashboard 経由の run では一度も走らない）。

### 直した不整合

1. `design-document-format` が作業ルールのトグル一覧に並んでいたが、実際は id 直引きで読むので
   トグルが効かなかった → 契約は一覧から外し、「いま有効な書式」として表示する
2. 同じものが実装フローの工程の追加ルール候補にも出ていた → 候補は `kind: rule` だけにする
3. 工程の追加ルールが 1 件しか選べなかった（ラジオ） → 複数選択にし、ノード定義は
   `method`（単数）から `methods`（配列）へ。旧定義は読み込み時に配列化して互換を保つ
4. 同梱の既定 ON が状態表示に反映されなかった → 端末設定の宣言が無ければカタログ自身の既定を読む

## 第 5 段 — per-task ルールを planner・評価役へ渡す

第 4 段の時点で「工程ごとに選ぶルール」は、ダッシュボードの編集画面で人がノードを選んで
初めて効く仕組みだった。`type: auto`（planner がグラフ自体を組み立てる、最も一般的な使い方）
や、評価役が実行時に足すタスク（検証 fail の作り直し・データ駆動 fan-out の map）には、
per-task ルールを選ぶ手段が無かった。第 5 段はこの手段を、既存の複製の枠組みのまま追加する。

### 設計

カタログの正典は変えない。run 専用 tuning.json（dashboard が `selection: "per-task"` の
定義を `enabled: false` のまま複製する）を、agent-flow（Python）が **直接読む**——
`agentcore.methods.select()` を経由しない別経路にする。理由は、`select()` は `enabled: true`
の項目を `when` 条件だけで全ノードへ一律に自動注入する仕組みで、「このタスクだけに」という
per-task の選択とは意味が違うため。同じファイルに両方を混ぜても、`enabled` と `selection`
というフィールドの意味を分けておけば安全に共存する。

1. **dashボード**: `perTaskMethodsSnapshot()` が per-task ルールの完全な定義（`fragments` 込み）を
   `enabled: false` で複製し、`submit()` が自動適用ルールと同じ tuning.json へ書き込む。
2. **agent-flow**: `patterns.py` の `_per_task_rule_catalog()` が同じ tuning.json を
   `selection == "per-task"` で絞って読む（`AGENT_TUNING_DIR` は methods.py の
   `_method_tuning_file()` と同じ解決）。`per_task_rule_directive()` が一覧（id + 説明）を
   planner／評価役のプロンプトへ後置する。
3. **選択の反映**: planner・評価役が返すタスク JSON に `"methods": ["<id>"]` を含められる。
   `_coerce_tasks`（3 つの生成経路すべての単一チョークポイント）が、選ばれた id のうち
   **そのノードの role に合う本文を持つもの**だけを `goal` へ複製する。role は
   `kind == "verify"` なら `"verify"`、それ以外は `"worker"`（`agentcore.methods.role_for` と
   同じ既定）。未知の id・role 不一致は黙って外す（フェイルオープン——LLM の書式ミスで
   run を止めない。カタログ側の壊れた宣言をフェイルクローズにする第 3・4 段とは対照的で、
   ここは「実行時に LLM が書く自由記述に近いフィールド」なので寛容にする）。

### 配線した場所

- `plan_strategy_agent`（組み込みエージェント planner）: `split_note`/`tier_note` と同じ
  「非空なら後置」の流儀。
- `continue_agent`（評価役。`continuation.py`）: `review_lens_directive()`/`tier_evaluator_directive()`
  と同じ「スキル/組み込み両経路への一律後置」の流儀（評価役プロンプトは外部スキルに委譲しない）。
- `plan_strategy_flow_planner`（flow-planner スキル）: 意図的に**配線しない**。スキルは
  リポジトリ外の独立プロセスで、この機能を知らない。既存の `split_policy` も同じ理由でこの
  経路には渡していない（既知の制約で、今回新たに広げるものではない）。

### tuning.json が無い環境

新しい設定キーは増やさない。`AGENT_TUNING_DIR` にファイルが無ければ
`_per_task_rule_catalog()` は空を返し、`per_task_rule_directive()` は空文字列——プロンプトは
1 バイトも変わらず、`_coerce_tasks` の `methods` 処理も no-op になる。dashboard を経由しない
CLI 単体利用（多くのテスト・多くの実運用）はこれまでどおり動く。

検証: `tools/agent-flow/tests/test_planner.py` の `PerTaskRuleTests`
（カタログの絞り込み・複製の役割一致・未知 id の無視・重複排除・両プロンプトへの後置・
カタログ無しでの no-op）。dashboard 側は `perTaskMethodsSnapshot` が `enabled: false` を
強制することと、`submit()` が自動適用ルールと同じ tuning.json へ複製することを
`tools/agent-dashboard/test/adhoc-flow.test.js` で確認する。
