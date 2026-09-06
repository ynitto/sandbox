# agent-app agent-loop統合タスク一覧 実装計画

> 作成: 2026-09-06  
> 対応設計: `docs/plans/2026-09-06-agent-app-agent-loop-task-catalog-design.md`

## 方針

agent-loopを設定探索、タスク正規化、実行、履歴の正典とし、statemachine-makerのIPCとrendererを薄い管理UI、
agent-appをリポジトリ文脈と共通実行設定の提供者に保つ。各段階を失敗する契約テストから始め、既存の会話、
tmux、ワークフロー領域を変更しない。

## Phase 1: agent-loopのタスクカタログ契約

### 1. 設定源と実効エントリの解決を固定する

対象:

- `tools/agent-loop/agent_loop/config.py`
- `tools/agent-loop/agent_loop/repository_ui.py`
- `tools/agent-loop/test/test_repository_ui.py`
- 必要なら`tools/agent-loop/test/test_config.py`

テストを先に追加する。

- リポジトリ直下、`.agents`、共通設定の探索順位
- 実効設定ファイルと関連する全設定源を返す
- `cwd`省略を選択リポジトリとして解決する
- 明示`cwd`、`~`、mapping参照を正規化する
- 選択リポジトリと異なるcwdのエントリを除外する
- 上位設定に隠された共通エントリを`effective: false`で返す

実装では、デーモン起動と同じ解決処理を共通関数へ抽出する。`repository_snapshot`独自の
`_load_prompt_file_data`参照を廃止し、共通関数の結果だけを使う。

### 2. ステートマシンと全エントリを結合する

対象:

- `tools/agent-loop/agent_loop/repository_ui.py`
- `tools/agent-loop/agent_loop/loopentry.py`相当の既存正規化モジュール
- `tools/agent-loop/test/test_repository_ui.py`

カタログ項目の最小契約を定義する。

```json
{
  "id": "task:...",
  "kind": "statemachine|prompt|hook|broken",
  "name": "表示名",
  "cwd": "/resolved/repo",
  "workflow": ".statemachine/name/workflow.yaml",
  "schedules": [],
  "effective": true,
  "source": { "scope": "repository|global", "path": "..." },
  "error": null
}
```

テスト対象:

- 未スケジュールのステートマシン
- 同じステートマシンを参照する複数エントリ
- 自由プロンプト、slashのみ、promptとslash、hooks、event_hook
- 存在しないステートマシン参照
- 同名エントリの別ID
- 履歴と実行状態のタスクへの関連付け

既存`machines[].schedule`は移行期間だけ互換フィールドとして残し、新UIは`tasks[].schedules`を使う。

## Phase 2: 複数保存先への安全な書き込み

### 3. 設定エントリの安定参照と競合検出を追加する

対象:

- `tools/agent-loop/agent_loop/repository_ui.py`
- `tools/agent-loop/test/test_repository_ui.py`

各エントリに設定パス、配列位置、正規化内容から`entryRef`と`fingerprint`を付与する。更新時は双方を要求し、
ファイルmtimeだけでなく対象内容の指紋も照合する。名前だけの更新を禁止する。

テスト対象:

- 同名エントリを個別更新できる
- 対象外エントリと未知トップレベル設定を保持する
- 読込後の外部変更を検出して上書きしない
- 一時ファイル失敗時に元ファイルが残る

### 4. 保存先選択、コピー、移動を実装する

対象:

- `tools/agent-loop/agent_loop/repository_ui.py`
- `tools/agent-loop/agent_loop/cli.py`
- `tools/agent-loop/test/test_repository_ui.py`

`agent-loop schedule --json`の要求へ次を追加する。

```json
{
  "destination": "repository|global",
  "operation": "save|copy|move",
  "entryRef": "...",
  "fingerprint": "..."
}
```

- `repository`: 既存の実効ローカル設定があれば同じパス、なければ`<repo>/.agents/agent-loop.yaml`
- `global`: agent home配下の既存設定、なければ`~/.agents/agent-loop.yaml`
- global保存では正規化cwdを必ず設定
- globalから新規localを作る場合は現在の実効設定全体を引き継いでから更新
- moveは宛先の保存成功後に元を削除
- 実効設定を変えた場合だけdaemonへreloadを送る

複数の定期設定を許可し、従来の「同じステートマシンの予定が複数なら拒否」を削除する。新規追加と編集を
区別し、対象エントリだけを変更する。

## Phase 3: 共通設定付きの一回実行

### 5. agent-loopへ機械向け一回実行境界を追加する

対象:

- `tools/agent-loop/agent_loop/cli.py`
- `tools/agent-loop/agent_loop/repository_ui.py`または新規`repository_run.py`
- agent-loopの既存dispatch、hook、statemachine実行モジュール
- `tools/agent-loop/test/test_repository_ui.py`
- 必要なら新規`tools/agent-loop/test/test_repository_run.py`

JSON標準入力を受ける管理UI向けコマンドを追加する。rendererがコマンド文字列を組み立てないよう、
statemachine-makerのadapterからだけ呼ぶ。

要求にはtask ID、fingerprint、入力、agent CLI、model、共通指示、開始アクションを含める。設定ファイルは
変更せず、解決したエントリへ一回限りの実行オーバーレイとして適用する。

テスト対象:

- ステートマシン本体だけの手動実行
- 定期エントリと同じ入力での手動実行
- 自由プロンプト、slash、hookの手動実行
- agent/modelの一時上書き
- 共通指示、開始コマンド、開始スキルの順序
- 起動済み設定や定期設定を変更しない
- 停止、成功、要確認、失敗、履歴記録
- 古いtask fingerprintと対象外cwdを拒否する

### 6. agent-appの実行設定を安全なオーバーレイへ変換する

対象:

- `tools/agent-app/src/main/automation/ipc.js`
- `tools/agent-app/src/main/sessionSetup.js`
- `tools/agent-app/src/main/settings.js`
- `tools/agent-app/test/session-setup.test.js`
- `tools/agent-app/test/settings.test.js`

会話とタスクで、共通指示と開始アクションの正規化を共有する。タスク実行では会話セッションの
`cliSessions.setupApplied`を流用せず、一回実行の開始フェーズとして毎回適用する。実行方針の解決は
既存`settings.resolve`を使い、直接指定を最優先とする。

## Phase 4: statemachine-maker管理UI

### 7. IPC/preload契約をタスクカタログへ更新する

対象:

- `tools/statemachine-maker/src/main/agent-loop.js`
- `tools/statemachine-maker/src/main/ipc.js`
- `tools/statemachine-maker/src/preload.js`
- `tools/statemachine-maker/test/agent-loop.test.js`
- `tools/statemachine-maker/test/preload-contract.test.js`

次の操作を公開する。

- task catalog取得
- task一回実行、停止
- schedule追加、編集、コピー、移動
- task履歴とログ取得
- 設定ファイルをOSで開く

既存workflow編集APIとAI workflow APIは維持する。

### 8. 一覧と種類別詳細を実装する

対象:

- `tools/statemachine-maker/src/renderer/renderer.js`
- `tools/statemachine-maker/src/renderer/styles.css`
- `tools/statemachine-maker/test/app.test.js`

既存`executionMachines()`中心の画面を`tasks`カタログ中心へ置き換える。

- 全種類を一つの一覧へ表示
- 要対応、実行中、次回予定、名前の順で並べる
- 種類、状態、次回予定を文言で表示
- 種類ごとに`手順`または`内容`または`設定`タブを出す
- 参照エラーは実行を無効化し、回復操作を出す
- 未適用の共通予定をタスク詳細へ残す

既存のステートマシン編集画面は`手順`タブから開く。

### 9. 手動実行条件と定期実行編集を実装する

対象:

- `tools/statemachine-maker/src/renderer/renderer.js`
- `tools/statemachine-maker/src/renderer/styles.css`
- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/automation-frame.html`
- `tools/agent-app/test/app.test.js`

親agent-appから、利用可能なagent/model、実行方針、現在の共通設定をiframeへ渡す。手動実行欄に会話と
同じ4方針を表示し、直接指定時だけagent/modelを展開する。

定期実行編集には保存先の2択を先頭に置く。移動時だけコピー／移動を確認し、通常の保存に確認ダイアログを
追加しない。保存ボタン自身を処理中、成功、失敗へ変化させる。

### 10. 会話開始前後のレイアウトを固定しEnter仮想キーを追加する

対象:

- `tools/agent-app/src/renderer/index.html`
- `tools/agent-app/src/renderer/styles.css`
- `tools/agent-app/src/renderer/renderer.js`
- `tools/agent-app/src/renderer/term.js`
- `tools/agent-app/test/app.test.js`
- `tools/agent-app/test/input-mode.test.js`

会話画面の開始前と開始後を同じGrid骨格へ揃える。中央面、履歴行、composerのDOM順を固定し、状態変更では
中央面の内容と可視性だけを変える。`history-only`によるflex配分の切替を廃止する。

テストを先に追加する。

- 下書き状態とtmux接続後でcomposerのDOM位置とGrid行が変わらない
- 開始前も入力モード切替の幅と場所が維持され、端末操作だけが無効になる
- 送信待ち、成功、失敗でcomposer外形が変わらない
- 狭い高さでは中央面だけが縮む
- terminal key toolbarにEnterとaria-labelがある
- Enterが`\r`を一回だけtmuxへ送り、端末モードとフォーカスを維持する

CSSでは中央面を`minmax(0, 1fr)`、履歴行とcomposerを`auto`にし、スクロール領域へ
`scrollbar-gutter: stable`を指定する。開始前は端末とは別のニュートラルな開始面を同じGridセルに表示する。
仮想キーボードではEnterをTabより広い主要キーとして追加する。

## Phase 5: 配布同期と検証

### 11. file dependencyとvendorを同期する

対象:

- `tools/agent-app/package.json`
- `tools/agent-app/vendor.js`
- `tools/agent-app/node_modules/statemachine-maker`の生成物

statemachine-makerのsourceを正としてagent-appのfile dependencyを更新し、vendorコピーを再生成する。
`node_modules`を手編集しない。同期後にsourceと配布物のハッシュまたは契約テストを確認する。

### 12. 自動テストを実行する

順序:

1. agent-loopの対象Pythonテスト
2. statemachine-makerのNodeテスト
3. agent-appのNodeテスト
4. `git diff --check`

最低条件:

- 新規domain分岐90%以上
- 既存テスト全件成功
- 設定保存の失敗系と競合系を含む
- 実際のtmux/Electronを使う既存統合テストを維持

### 13. Electronスモーク試験を実行する

fixtureで次を確認する。

- ステートマシンと複数予定が1タスクへ結合される
- 自由プロンプトとフックが同じ一覧へ出る
- リポジトリ切替でcwd一致タスクだけへ切り替わる
- 手動実行で方針、agent、modelを一時指定できる
- 共通指示、開始コマンド、開始スキルが実行へ届く
- repository/globalへの保存、未適用表示、コピー、移動
- 外部編集競合、参照切れ、設定構文エラーから回復できる
- キーボード操作、狭幅、コンソールエラーなし
- 会話開始前後で入力欄の位置と寸法が変わらない
- 仮想EnterでCLIの入力を確定できる

Windows/WSL実機試験はリリース必須条件にしない。パス変換とホスト側比較は自動テストで固定する。

## 完了条件

- 選択リポジトリの全タスク種別を一つの一覧で確認できる。
- ステートマシンと複数定期設定が重複せず結合される。
- 定期設定の保存先をrepository/globalから選び、適用状態を確認できる。
- 全タスクを会話と同じ実行方針・agent・modelで手動実行できる。
- 共通指示、開始コマンド、開始スキルがタスク実行にも効く。
- agent-loopとagent-appの設定解決、実行、履歴が一致する。
- 自動テストとElectronスモーク試験が成功する。
