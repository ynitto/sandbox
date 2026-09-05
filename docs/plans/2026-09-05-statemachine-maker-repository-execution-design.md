# statemachine-maker リポジトリ実行画面設計

> 決定日: 2026-09-05  
> 対象: `tools/statemachine-maker`、`tools/agent-loop`  
> 関連: `2026-09-05-statemachine-maker-ui-redesign-design.md`、`2026-09-05-statemachine-maker-ai-assistance-design.md`、`../specs/agent-loop-spec.md`

## 背景

statemachine-maker のリポジトリ画面は、現在はステートマシンを作成・編集する入口であり、実行は編集画面の「テスト・実行」ダイアログに閉じている。どのステートマシンが動いているか、最後にどう終わったか、次にいつ動くかをリポジトリ単位で確認できない。

一方、agent-dashboard の定常業務には、実行、定期設定、状態、履歴、ログを扱う機能がある。ただし、その画面と YAML 読み書きを statemachine-maker へ複製すると、用途の異なる機能が混ざり、agent-loop の設定契約とも二重化する。

本変更では、リポジトリ選択後の既定画面をステートマシンの実行ハブにする。statemachine-maker は選択、入力、状態表示に専念し、手動以外の実行基盤は agent-loop に一任する。手動実行も agent-loop の単発コマンドを入口にして、agent-tools の共通ハーネスと同じ結果契約を使う。

## 目的

- リポジトリ内のステートマシンを、一覧から選んですぐ実行できる。
- 実行中の工程、結果、次回予定、履歴、ログを一つの画面で確認できる。
- 日次、週次、一定間隔の定期実行を少ない入力で設定できる。
- maker に scheduler、cron 解釈、agent 選択、実行履歴の別実装を作らない。
- 既存の固定配色と簡潔な文言を保ち、画面幅による横スクロールを発生させない。

## 過去の設計との関係

- リポジトリを先に選び、その内容を右側へ表示する現在の情報構造は維持する。
- AI の新規作成と見直しは、候補を未保存データへ取り込み、人が保存する既存設計を維持する。
- 実行に使う AI は agent-tools の `agents/*.json` と `control.json` で解決し、maker 固有のモデル一覧や選択規則は作らない。
- agent-dashboard で採用済みの「選択を先に置き、概要、主操作、詳細の順で見せる」「履歴とログは必要時に読む」という判断を再利用する。
- agent-loop の Scheduler は唯一の dispatch gate である。定期発火、重複制御、実行経路選択を maker に移さない。

## 参考にした製品パターン

- GitHub Copilot の agent sessions は、セッション一覧から一件を選び、進捗、所要時間、ログ、停止、追加入力を同じ詳細面で扱う。  
  https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents
- OpenAI Codex app は、作業を project 単位で整理し、選択した task の進捗と成果確認を中心に据える。automations の結果もレビュー可能な作業として扱う。  
  https://openai.com/index/introducing-the-codex-app/
- Cursor の background agents は、一覧から agent を選択し、状態を確認しながら follow-up または takeover する構造を採る。  
  https://cursor.com/docs

採用する共通原則は「一覧と詳細」「一つの明確な主操作」「進捗と結果を同じ場所で見る」「高度な情報は必要時だけ開く」である。会話 UI、分析カード、装飾的なグラフは持ち込まない。

## 検討した案

| アプローチ | UI の簡潔さ | agent-loop との整合 | 保守性 | 実装量 | 採否 |
|---|---:|---:|---:|---:|---|
| A. 薄い実行ハブ＋agent-loop の機械可読 API | 高 | 高 | 高 | 中 | 採用 |
| B. maker が agent-loop YAML を直接解析・更新 | 高 | 低 | 低 | 小 | 却下 |
| C. agent-dashboard の定常業務画面を直接移植 | 低 | 中 | 低 | 大 | 却下 |

A は maker を表示層に保ち、実行条件、設定検査、状態、履歴の正典を agent-loop に置ける。B は初期実装が小さいが、すでに agent-dashboard に存在する YAML 読み書きの二重化をさらに増やす。C は機能量が過剰で、statemachine-maker の単純な作成・実行という目的と合わない。

## 情報構造

リポジトリ選択後の右領域に `実行` と `ワークフロー` の二つのタブを置き、`実行` を既定にする。

```text
┌ リポジトリ ───┬────────────────────────────────┐
│ repo-a         │ repo-a                         │
│ repo-b         │ [実行] [ワークフロー]    新規作成 │
│                ├───────────┬────────────────────┤
│                │ マシン一覧  │ 選択中のマシン         │
│                │           │ 名前・目的              │
│                │ ● 実行中   │ [今すぐ実行] [編集]     │
│                │ ○ 定期     │                        │
│                │ ○ 手動     │ 現在の状態／入力         │
│                │           │ 最終実行・次回・使用 AI   │
│                │           │ 定期実行                 │
│                │           │ 履歴・選択ログ            │
└────────────────┴───────────┴────────────────────┘
```

### リポジトリ見出し

- リポジトリ名と省略可能なパスを表示する。
- タブは `実行`、`ワークフロー` の二つだけにする。
- `新規作成` は既存の `AIで下書き` と `手動で作成` を開くメニューとする。
- 実行環境はアプリ上部の既存操作に残し、リポジトリ画面へ重複配置しない。

### ステートマシン一覧

- カードのマトリクスではなく、名前、短い状態、次回予定を持つ一列の選択リストにする。
- 状態は色だけに頼らず、`実行中`、`定期`、`確認待ち`、`エラー`、`未実行` の文言を併記する。
- 選択中の項目は背景と枠で示す。
- 検索、並び替え、タグは初版に入れない。

### 実行詳細

表示順を固定する。

1. 名前、目的、`今すぐ実行`、`編集`
2. 実行中だけ出る進捗領域
3. 実行前に必要な入力欄
4. 最終実行、次回実行、実効 AI
5. 定期実行
6. 直近の履歴と選択したログ

主操作は `今すぐ実行` の一つとする。実行中は同じ位置を状態表示に替え、`停止` を副操作として出す。編集画面の「テスト・実行」ダイアログは廃止し、この実行詳細へ遷移する。

## 手動実行フロー

1. 選択した workflow が参照する入力変数を agent-loop から取得する。
2. 実行時に必要な欄だけを表示し、未使用の自由入力や高度な引数を見せない。
3. 利用者が `今すぐ実行` を押す。
4. maker の main process がシェルを介さず `agent-loop statemachine --workflow ... --param ... -d ...` を起動する。
5. 行単位の進捗と実行ログを画面へ送り、最後の `RESULT {json}` を結果契約として読む。
6. 完了後に snapshot を再取得し、状態、履歴、ログを更新する。

入力値は renderer が構文解釈しない。workflow と entry の入力面は `agentcore.loopentry` と statemachine ハーネスが検査する。実効 AI は agent-tools の定義と agent-control の選択規則から解決し、画面はその結果を表示する。

## 定期実行フロー

### 画面で扱う項目

- 有効／無効
- 日次: 時刻
- 週次: 曜日と時刻
- 一定間隔: 分単位
- workflow が必要とする入力条件

任意 cron、hook、webhook、session、target、Ralph、acceptance などは編集しない。既存 entry が画面の範囲を超える設定を持つ場合は値を保持し、`高度な設定があります` と設定ファイルへの導線だけを表示する。

### 保存と反映

1. maker は workflow、表示用 schedule、入力条件だけを JSON で agent-loop へ渡す。
2. agent-loop が workflow の実在、入力キー、組合せ、時刻・間隔、既存設定との衝突を検査する。
3. agent-loop が対象 entry だけを更新し、他の top-level 設定と entry を保持して原子的に保存する。
4. daemon 稼働中なら transactional reload を要求し、反映結果を返す。
5. daemon 停止中なら設定を保存し、`保存済み・自動実行は停止中` と返す。
6. 初回有効化時は `保存して自動実行を開始` を主操作にし、agent-loop を非アタッチで起動する。

maker を閉じても daemon は終了させない。無効化は entry を無効にして reload するだけで、他の定常業務を持つ daemon 自体は止めない。

## agent-loop の機械可読境界

既存の人向け標準出力を maker が解析しない。agent-loop に次の責務を持つ安定した JSON 境界を追加する。具体的な CLI 名は実装時に既存 argparse と衝突しない形で固定し、契約テストで守る。

### Snapshot

リポジトリを指定し、次を返す。

- agent-loop の利用可否とバージョン
- daemon の `running`、`paused`、設定 revision、再起動要否
- statemachine workflow と対応する定期 entry
- entry の有効状態、表示用 schedule、次回時刻
- 実効 agent CLI と model
- workflow ごとの active run
- 直近の実行記録

### Schedule update

workflow の相対参照、entry 名、有効状態、表示用 schedule、入力値を受け取る。応答は少なくとも `saved`、`applied`、`daemonRunning`、検査エラーを返す。設定の読み書き、cron 変換、入力面との照合は agent-loop 内だけで行う。

### Run journal

手動と定期の両方を同じ記録へ追記する。記録はリポジトリの実体パスから作った識別子で agent-loop の状態ディレクトリへ保存し、リポジトリへ生成ファイルを増やさない。

各記録は `runId`、workflow 参照、entry、開始・終了時刻、起動種別、結果、最終状態、停止理由、実効 AI、詳細 `logFile` を持つ。既定で新しい 200 件を保持する。maker は agent-loop が snapshot で返した記録と、許可済み `logFile` の末尾だけを読む。

## 実行状態と重複制御

- 定期発火、slot、busy、overlap は既存 Scheduler の dispatch gate を通す。
- 同じ workflow の手動実行と定期実行が衝突しないよう、agent-loop が workflow 単位の実行ロックを持つ。
- 実行中なら新しい手動実行を開始せず、現在の実行を表示する。
- maker から始めた手動実行は子プロセスの停止を要求できる。
- 定期実行は agent-loop の `cancel` を使い、管理外プロセスを maker が直接終了しない。
- renderer は `requestId` と `runId` で古い進捗イベントを破棄する。

## 結果とエラー表示

| 状態 | 表示 | 主な回復操作 |
|---|---|---|
| 成功 | `完了` と最終状態 | ログを見る、再実行 |
| 実行エラー・終了コード 1 | `エラー` | 詳細を見る、定義を編集 |
| 検査枯渇・終了コード 3 | `人の確認が必要` | 落ちた検査を見る、編集または上位 AI で再実行 |
| daemon 停止 | `自動実行は停止中` | 自動実行を開始 |
| 保存済み・reload 未反映 | `保存済み・未反映` | 再反映 |
| agent-loop 不在・契約不一致 | `実行環境を更新してください` | 診断と更新手順を見る |
| 入力不足・不正設定 | 欄の直下に具体的な理由 | 入力または設定を修正 |
| 重複実行 | 現在の実行を表示 | 完了を待つ、停止 |

失敗時に生の stderr を主文として出さない。短い原因と次の操作を先に示し、コマンド、終了コード、ログ末尾は `詳細` に置く。`RESULT` が無い正常終了は成功扱いにしない。

## レスポンシブとアクセシビリティ

- 広い画面では `リポジトリ / マシン一覧 / 実行詳細` の三領域を表示する。
- 中幅ではマシン一覧を詳細上部の選択欄へ畳む。
- 狭幅ではリポジトリも選択欄へ畳み、実行詳細を一列にする。
- schedule の二列フォームは狭幅で一列にする。
- 本文、パス、ログは `min-width: 0` と折返しを持ち、ページ全体の横スクロールを禁止する。
- loading は `role=status`、実行・設定エラーは `role=alert` を使う。
- focus、選択、状態は色だけに依存しない。
- 既存のニュートラル配色と青い主操作色を固定し、色・テーマ設定を追加しない。

## コンポーネント

### statemachine-maker main

- agent-loop の場所と契約バージョンを解決する adapter を追加する。
- snapshot、schedule update、daemon start/reload、manual run/stop、log tail を IPC として公開する。
- 登録済みリポジトリ以外の cwd を拒否する。
- workflow は選択リポジトリ配下の相対パスだけを許可する。
- 子プロセスは argv で起動し、shell を使わない。

### statemachine-maker renderer

- home を `run` と `workflows` のタブ状態へ分ける。
- repository、machine、run を別々の選択状態として持つ。
- snapshot の loading、ready、stale、error を持つ。
- 実行中は progress を差分更新し、完了後だけ snapshot を取り直す。
- 履歴選択まで詳細ログを読まない。

### agent-loop

- snapshot と schedule update の JSON 契約を追加する。
- 設定の検査、対象 entry の保存、daemon reload の結果を一つの境界で扱う。
- statemachine の手動・定期結果を共通 journal へ記録する。
- workflow 単位の実行ロックと active run を状態 snapshot に出す。
- 既存 `RESULT` 契約、Scheduler、`agentcore.loopentry`、agent-tools harness を正典として再利用する。

## セキュリティとデータ境界

- snapshot、実行、設定変更の root は maker に登録された実在ディレクトリだけに限定する。
- workflow の絶対パス、`..`、`~`、root 外への symlink 解決を拒否する。
- schedule update は許可したフィールドだけを更新し、任意 YAML や任意コマンドを受け取らない。
- 入力値とログは HTML として解釈せず、renderer で常にエスケープする。
- ログ読み出しは run journal が返した実在パスに限定し、サイズ上限を設ける。
- agent CLI と model は agent-tools の登録済み定義から解決し、画面入力を argv 名として信用しない。

## テスト

### agent-loop 契約

- snapshot が workflow、entry、schedule、daemon、active run、history を正規化して返す。
- 日次、週次、一定間隔を正しい entry へ変換する。
- 不正 schedule、入力キー不足、禁則組合せ、root 外 workflow を拒否する。
- 対象外の top-level 設定と entry を保持する。
- 設定保存を原子的に行い、検査失敗時に元ファイルを変えない。
- daemon 稼働、停止、reload 成功、reload 失敗を区別する。
- 手動と定期が同じ run journal を作り、保持上限を守る。
- 同じ workflow の重複実行を拒否し、別 workflow は既存 slot 規則に従う。
- 終了コード 0、1、3 と壊れた／欠けた `RESULT` を区別する。

### maker 単体・IPC

- preload の公開 API と IPC channel が対応する。
- 未登録 root、root 外 workflow、未承認 log path を拒否する。
- agent-loop の argv、stdin JSON、停止、末尾の改行なし出力を検査する。
- 古い requestId/runId のイベントを破棄する。
- agent-loop 不在、古い契約、daemon 停止、保存済み未反映を画面用状態へ変換する。

### UI・実機

- リポジトリ選択直後に実行タブが開き、最初の workflow が選択される。
- 実行、停止、成功、エラー、人の確認が必要、再実行を操作できる。
- 必要な入力欄だけが表示される。
- 定期実行の有効化、編集、保存、未反映からの再試行ができる。
- 履歴選択時だけログが読み込まれる。
- workflow がない、agent-loop がない、履歴がない各空状態から次の操作へ進める。
- 720px、980px、1440px で文字切れとページ横スクロールがない。
- キーボードだけでリポジトリ、タブ、workflow、実行、設定、履歴を操作できる。

## 実装順序

1. agent-loop の snapshot、schedule update、journal、実行ロックを契約テストから追加する。
2. maker に agent-loop adapter と IPC/preload 契約を追加する。
3. home を `実行` と `ワークフロー` に分け、実行一覧と詳細の静的状態を作る。
4. 手動実行、進捗、停止、結果、履歴、ログを接続する。
5. 定期設定、daemon 状態、起動、reload 結果を接続する。
6. 既存の「テスト・実行」ダイアログを削除し、編集画面から実行詳細へ戻す。
7. 空状態、失敗状態、狭幅、キーボード操作を仕上げる。
8. agent-loop、maker、Electron 実機の回帰テストと README を更新する。

## 既存スキルとの関係

- `brainstorming`: 既存設計を確認し、薄い実行ハブ、maker 直接 YAML 編集、dashboard 移植を比較した。
- `ui-designer`: 一覧と詳細、単一の主操作、段階表示、狭幅での一列化、回復可能なエラー表示を設計した。
- `ltm-use`: agent-dashboard と statemachine-maker で採用済みの情報構造と責務分離を再利用した。
- `moltbook-use`: LTM の連邦補完として検索を試みたが、接続設定が無いため外部知見は取得していない。
- `agent-loop`: 定期発火、実行経路、重複制御、状態、履歴の実行基盤。
- `agent-tools`: agent CLI と model の解決、statemachine harness、結果契約の正典。
- `statemachine-use`: workflow と入力・遷移・検査の定義契約。

実装時は、境界テストを先に固定する `test-driven-development`、終了コード、停止、設定未反映を扱う `failure-driven-development`、実機の幅と操作を確認する `webapp-testing` が相互補完する。どのスキルも maker 固有の実行ハブを単独で代替しない。

## 非目標

- maker 独自の scheduler、cron evaluator、daemon
- maker 固有の AI／model 登録・選択規則
- 汎用 agent chat
- 任意 cron、hook、webhook、Ralph、target のビジュアル編集
- tmux または terminal の埋め込み
- 分析ダッシュボード、集計グラフ、費用分析
- 色、テーマ、カード外観のカスタマイズ
- agent-dashboard の renderer または YAML parser の直接移植
- ステートマシン編集画面自体の再設計

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-05 |
| 決定者 | ユーザー |
| 採用案 | リポジトリ既定の薄い実行ハブ＋agent-loop の機械可読境界 |
| 却下案 | maker による agent-loop YAML の直接管理、agent-dashboard 定常業務画面の直接移植 |
| 主な理由 | 一般的な agent app の一覧・詳細・監督パターンを保ちながら、定期実行、設定検査、状態、履歴を agent-loop の責務として一元化できるため |
| UI 決定 | `実行` を既定タブにし、workflow 一覧と実行詳細を分離する。主操作は `今すぐ実行`。定期設定とログは段階表示する |
| 実行決定 | 手動は daemon 不要の agent-loop statemachine、手動以外は agent-loop Scheduler、AI 解決と harness は agent-tools を正典とする |
| トレードオフ | agent-loop に JSON snapshot、設定更新、run journal、workflow lock を追加するため、maker だけの変更より実装範囲が広い |
| 再評価条件 | agent-loop が別の管理 API を正式提供した場合、agent-dashboard と共有できる独立 UI package が成立した場合、または任意 cron・複雑な定常業務編集が maker に必要になった場合 |
