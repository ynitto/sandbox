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

## 第 3 段 — エンジンの汎用インターフェースと、カスタマイズできる振る舞いの分離

第 1・2 段の実装は、統合検証の文言・面の語彙と対応・レビュー観点・分割単位の指示文・設計成果の
必須節をエンジン側の固定値として持っていた。第 3 段では「エンジンは共通性の高いインターフェース
（宣言の置き場と解決規則）だけを持ち、具体的な振る舞いはカスタマイズで差し替えられる」形へ分けた。

| 仕組み | エンジン（共通インターフェース） | カスタマイズ（振る舞いの置き場） |
|--------|--------------------------------|--------------------------------|
| 面（surface）と作業ルール | 手法の `when.surfaces` 宣言を集めて語彙とルール束を導出し、plan 生成で goal へ複製（フェイルクローズ）。正規化は面 id の形式だけを見る | 同梱 `methods/ui-consistency.json`（`ui`）・`methods/test-green-evidence.json`（`test`）。リポジトリの `.agents/methods/` は同じ宣言で**面の追加**と**同 id 上書き**ができる |
| 統合検証 | 終端へ verify + 再検証を付ける規則と、カタログから内容を解決する `terminalVerification`（引けなければ標準装備を諦める——固定文言のフォールバックで正典を二重化しない） | 同梱 `methods/integration-verify.json` が検証内容の正典。リポジトリ同 id 上書きで「この repo の検証手順」へ差し替え |
| 設計成果の契約 | `contract: { sections, items }` の正規化（`normalizeContract`）と解決（`resolveContract`）。終端 goal の指示文と、設計セッション・作業準備の取り込み判定が**同じ解決結果**を使う | 既定契約（必須4節＋変更対象の強制レイヤー）は `design-contract.js` の同梱定義。カスタム設計フローは定義の `contract` で節・項目を宣言できる（digest 対象・snapshot に固定） |
| 分割単位（P2） | 「名前で選び、指示文を planner へ後置する」規則（`split_policy` / `split_policy_directive`） | 同梱 `behavior` / `file`。設定 `split_policies`（`{名前: 指示文}`）で追加・同名上書き |
| レビュー観点（P4） | 「観点を評価役プロンプトへ後置し、観点 key を evaluate イベントへ残す」規則（`review_lenses` / `review_lens_directive`） | 同梱 3 観点（二重実装・表現差異・文言量）。設定 `review_lenses`（`[{key, label, detail}]`）で構成ごと差し替え |
| 公開後の CI（P6） | 第 2 段の `ci_status_command` 契約のまま（宣言コマンドの JSON 出力が正典）——既に分離済み | 利用者の宣言コマンド |

分離の共通規則:

- **正典は 1 箇所**。エンジンはフォールバック文言を持たない（カタログから引けない統合検証は
  null、面は起動失敗）。固定値の複製が残ると、カスタマイズしたのに古い文言が出る事故になる。
- **複製で固定**。面のルール・統合検証・設計契約はいずれも plan / snapshot へ複製されるので、
  カスタマイズを後から変えても保存済み run・準備項目の振る舞いは変わらない。
- **壊れた宣言の扱いは置き場で変える**。保存時に検査できるもの（フロー定義の contract）は弾き、
  保存済み状態から読むもの（session / 準備項目の snapshot）は黙って既定へ落とす（開けなくしない）。

検証は本書末尾のコマンドに含まれる（dashboard: 面のカタログ導出・リポジトリ上書き・契約宣言、
agent-flow: `split_policies` / `review_lenses` の追加・上書き・不正値の既定回帰）。
