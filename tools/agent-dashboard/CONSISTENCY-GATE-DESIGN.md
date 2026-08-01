【差別化の切り口】現在の結線状態と失敗時点の記録を別の時系列として扱い、誤断定と done の迂回を防ぐ。

# dashboard 一貫性ゲート表示設計

## TL;DR

dashboard は、概要で `regression_cmd` と `intake_cmd` の現在の結線状態を見せ、要対応で codd-gate 由来と確認できる失敗の意味を見せる。未結線なら公式の設定例と sibling CLI を案内するだけで、設定もタスク状態も変更しない。

主要な判断は三つ。全体状態は概要、個別失敗は要対応に置く。`設定あり` と `codd-gate 結線済み` を分ける。過去の失敗原因は失敗時の `failure-*` と記録コマンドから判定し、現在の設定から推測しない。

自動設定、codd-gate の実行、新しい dashboard 専用フックは採らない。agent-project の完了判定と公式ファイル契約を二重実装するためだ。

読むべき人は dashboard と agent-project の保守担当者。codd-gate 自体の実装担当者には、CLI 契約の確認箇所以外は不要である。

## 前提と完了条件

現 HEAD には必要な表示経路がすでにある。この文書は追加実装案ではなく、今後の変更で守る表示契約と検収基準を一つに固定するものとした。

完了条件は、表示項目、未設定時の導線、done 不変条件、公式契約の境界、テスト観点が、t3 と t4 の調査結果および現物コードに対応していること。`未設定` はキーが空の場合だけを指さない。汎用フックに別コマンドが入っていて codd-gate の正規形に合わない場合も `未結線` とする。

正典は agent-project が生成する設定、`needs/`、`inbox/`、`commands/` と、agent-project README および CLI parser である。dashboard README や画面文言はその利用者であって正典ではない。

## 範囲

目標は、人が画面だけで両フックの設定有無、結線状態、ゲート失敗の意味、次に行う手作業を判断できること。判定不能な場合は不明と分かり、生の記録へ戻れることも含む。

対象外は、agent-project のフック実装、dashboard 専用 IPC の追加、設定や needs の自動更新、codd-gate の実行、UI 操作による done 確定である。

## 表示項目

抽象度:コンポーネント。

| 配置 | 表示 | 根拠 |
|---|---|---|
| 概要「一貫性ゲート」 | 全体の `結線済み`、`一部結線`、`未結線` | `consistencyGate.wired` と各 `*Wired` |
| 同上 | `regression_cmd` と `intake_cmd` ごとの `設定:あり/なし`、`結線済み/未結線`、現在値 | `*Configured`、`*Wired`、`*Cmd` |
| 同上 | regression 失敗は done 確定を止めること、intake 成功時はドリフトを修復タスクへ積むこと | agent-project のフック契約 |
| 同上 | 自動探索した設定候補であり、実効 `--config` と一致する保証がないこと | `configSource`、`explicitConfigUnknown` |
| 要対応の既存「状況」 | 検証失敗の要約、対処、分類、対象、コマンド、作業場所、終了コード | `needs/<id>.md` の構造化 `failure-*` |
| 同上 | codd-gate の実行経路が regression かタスク自身の verify か、`intake_cmd` が未結線なら概要への案内 | `failure-phase` と記録された `codd-gate verify` |

現在値は未結線でも隠さない。たとえば `regression_cmd: make -s smoke` は `設定:あり、未結線` と表示し、一貫性ゲートの検査ではないと添える。キーの存在だけで有効とする案は却下する。

要対応では失敗要約と対処を先に、ゲート説明を後に置く。現在の `regression_cmd` が結線済みという理由だけで過去の失敗を codd-gate 由来にする案も却下する。設定変更後に過去の意味まで変わってしまうからだ。

## 未結線時の導線

抽象度:画面仕様。

未結線のキーだけ、次の正式値を提示する。`<root>` は利用者が対象プロジェクトのルートへ置換する。

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'
```

YAML では該当トップレベルキーを追加または置換する。JSON では既存トップレベル object の同名プロパティだけを追加または置換し、ファイル全体を置換させない。別コマンドが設定済みなら、置換で現在の処理を失うと警告する。両方必要な場合のコマンド合成は利用者が判断する。

`regression_cmd` が未設定で既存 YAML を読めた場合に限り、リポジトリルートから次を実行する選択肢を示す。

```bash
python3 tools/agent-project/codd_gate_regression.py \
  --config <状態 clone>/agent-project.yaml
```

この CLI は既存 YAML の `regression_cmd` 一つだけを冪等更新する。`--dry-run` は無変更確認に使える。`intake_cmd` 用の注入 CLI はないため、設定を直接編集する。設定ファイル未検出、JSON、既存 `regression_cmd` ありのいずれかなら CLI を勧めない。

画面の操作は、自動検出した設定ファイルを OS のエディタで開くところまで。設定作成、保存、CLI 実行は利用者が行う。

## done 不変条件

抽象度:境界。

`regression_cmd` はタスク verify が通った後、done 確定前に agent-project が実行する。失敗時は `_settle_done()` へ進まず needs を生成する。dashboard はこの結果を読むだけであり、有効化導線から approve、complete、status 更新を送ってはならない。

`intake_cmd` の出力は agent-project が検証し、既存の `enqueue_reconciled()` を通して backlog 化する。dashboard が検出結果を直接 backlog や done へ書く経路は設けない。既存の人操作が必要な場合も `commands/` または `inbox/` へ投函し、agent-project に判定させる公式経路を保つ。

設定ファイルを開くボタンは `openPath` だけを呼ぶ。表示更新は次の `readProject()` スナップショットで行い、楽観的に結線済みへ変えない。

## 公式契約の境界

抽象度:データ経路。

| 公式入力 | dashboard の読取経路 | 許される解釈 |
|---|---|---|
| `agent-project.{yaml,yml,json}` | `readToolConfig()` → `consistencyGateStatus()` → `readProject().consistencyGate` → `dashboard:project` | 設定有無とコマンド語順による結線判定まで |
| `needs/<id>.md` の `failure-*` | `parseNeeds()` → `needFailureViewModel()` → `renderNeedFacts()` | producer が記録した要約と実行時コマンド。旧票だけ限定的な後方互換解析 |
| `commands/*.err` と `commands/processed/*.json` | `listCommandFailures()` / `listCommandReceipts()` | 最新の失敗または受理。失敗表示を受理や送信待ちで覆わない |
| `commands/*.json` | ファイル存在と短い localStorage 表示 | `送信済み（取り込み待ち）` まで。処理成功とは呼ばない |
| `inbox/*.{json,md,markdown,txt}` | `readProject().inboxFiles` | backlog 化前の追加待ち件数。needs やゲート状態へ混ぜない |

dashboard は codd-gate の実在、バージョン互換性、実行成功を判定しない。また、agent-project が明示 `--config` で使うパスは現行 instance/status 契約にないため、自動探索候補との一致を断定しない。実効設定を正確に表示したくなった時点で、agent-project の公式契約を拡張する。

## 主要判断と却下案

### 概要と要対応へ分ける

概要は現在の全体状態、要対応は失敗時点の個別事実を担当する。技術情報欄へ集約する案は、対処したい人から遠い。両画面へ同じ説明を複製する案は、文言と状態がずれるため採らない。確信度は高い。

### 設定と実行記録を分ける

現在設定は結線の見込み、失敗記録は過去に実行された事実である。別モデルとして表示する。現在設定から過去原因を補完する案は誤断定を生むため却下する。代わりに情報が足りない旧票は不明のまま生ログへ案内する。確信度は高い。

### 有効化は人へ返す

dashboard は設定例、既存 CLI、設定ファイルを開く操作だけを提供する。自動書換は既存コマンドを失う恐れがあり、自動実行は dashboard が公式な完了判定を持つことになるため却下する。手作業は残るが、done 不変条件を一か所に保てる。確信度は高い。

## テスト観点

抽象度:検収。

1. 設定モデル:両方結線、片方だけ結線、両方未結線、空値、別コマンド、複数行 YAML、壊れた JSON で `configured`、`wired`、全体三値が一致する。
2. 概要 UI:現在値を隠さず、未結線の行だけ設定例を出す。YAML と JSON の編集指示、置換警告、CLI の表示条件、自動探索の注意、`openPath` だけの操作を確認する。
3. 要対応 UI:`failure-phase=regression` と記録された `codd-gate verify` がそろう場合だけ完了前の回帰検査と表示する。タスク verify は別経路と表示し、`codd-gate doctor`、`make smoke`、根拠なしでは断定しない。
4. 不変条件:回帰失敗が needs に残り done にならないこと、intake が agent-project の取り込み経路を通ること、表示コードから設定、needs、status を書かないことを確認する。
5. 契約同期:設定例を agent-project README と、`verify --base --repos`、`tasks --debt --repos`、sibling CLI の `--config` / `--dry-run` と照合する。

受入条件は、概要だけで現在の二つのフック状態を判断でき、要対応だけで記録済み失敗の意味と次の確認先を判断でき、どの導線からも dashboard が状態を確定しないこと。

## 検証記録

t3 のデータ経路報告と t4 の CLI 契約報告を、現 HEAD の `project.js`、`toolconfig.js`、`renderer.js`、`sections/overview.js`、`sections/needs.js`、agent-project の `mr.py` と `model.py`、対象テストに突き合わせた。

2026-08-01 に対象テスト 7 スクリプトを再実行し、すべて成功した。対象 7 ソースへの ESLint も成功した。設計書以外のファイルは変更していない。

## 未解決と範囲外

- 実効 `--config` パスと dashboard の自動探索候補が一致するかは、現契約では分からない。
- dashboard README/UI の CLI 例 `/path/to/.agents/agent-project.yaml` は、t4 が確認した正式な `<状態 clone>/agent-project.yaml` とずれている。本設計は正式な形を採用したが、実装と README の修正は対象外である。
- codd-gate の実在、互換性、実行成功の監視は対象外。必要なら agent-project の status 契約として追加する。
- 新規フック、設定注入 CLI、UI からの状態書換は行わない。

@followup dashboard README/UI の sibling CLI プレースホルダを `<状態 clone>/agent-project.yaml` に揃える。
