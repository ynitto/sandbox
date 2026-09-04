# statemachine-maker 設計 — 定常業務の作成を単体の Electron アプリへ切り出す

> 作成 2026-09-04 ／ 対象: `tools/statemachine-maker`（新設）
>
> agent-dashboard の定常業務作成（[手順ビルダー](./2026-09-03-agent-dashboard-routine-procedure-builder-design.md)・
> [操作の記録](./2026-09-04-agent-dashboard-routine-recording-feasibility.md)）を、dashboard に依らない
> 単体のアプリとして切り出す。成果物は statemachine-use スキルで動く `.statemachine/<識別名>/` で、
> アプリが無くても動く。

## 要件（依頼）

- statemachine-use スキルで動くステートマシンを作る。
- agent-dashboard の定常業務作成機能を切り出したもの。画面はライトトーン。
- ステップごとに何をやるか・どう遷移するかを分かりやすく見せる（ワークフローアプリ風にシンプルに）。
- playwright / winauto で人の操作を手本にステップを構築する。
- 成果物はステートマシン単体で動く。
- 既存のステートマシンを指定して編集できる。
- Windows / WSL の行き来は考えない。ただしステートマシンはどの OS でも動くように書く。

## dashboard との違い — YAML を書く

dashboard の手順ビルダーは「画面は工程列を組むだけ、YAML はスキルの作成モード（AI）が書く」と決めていた
（書式の正典を 2 か所に置かない）。本アプリは **決定的なコンパイラで YAML と actions を書く**。理由は 2 つ。

1. **既存の定義を編集する**には YAML を読み戻す必要があり、読める以上は書けなければ編集にならない。
2. **単体で動く成果物**を、AI の起動（エージェント CLI・端末・セッション）無しで得たい。

書式の正典がスキルにあることは変えない。守り方を「書かない」から「**書いた結果をスキル自身に検証させる**」に
変える——コンパイル結果はスキルの `engine.validate_workflow` と同じ規則の構造検査（JS）を通し、
最終判定は本物の `run_machine.py --dry-run`（結合テスト・画面の「検証」）で行う。

## 構成

```
tools/statemachine-maker/
  src/main/model.js        工程列 ⇄ 定義（コンパイル / 読み戻し / 構造検査 / 移植性の注意）。純粋
  src/main/recording.js    記録（playwright-cli のコード行 / winauto の JSONL）→ 工程列。dashboard と同じ規則
  src/main/store.js        .statemachine/<id>/ の一覧・読み・書き（maker.json の写しを併置）
  src/main/tools.js        道具の診断（python / スキルのスクリプト / playwright-cli / winauto）
  src/main/runner.js       外部コマンド（capture / stream / spawnRecorder）。シェルを介さない
  src/main/instruction.js  statemachine-use の作成モードへ渡す指示文（AI 補完）
  src/main/ipc.js, main.js, config.js
  src/preload.js           contextIsolation + sandbox。api.* の窓口
  src/renderer/            index.html / styles.css / renderer.js（ライトトーン・3 カラム）
  test/                    node:test。結合テストはスキルの run_machine.py を実際に呼ぶ
```

### 工程列（正規形 version 3）

dashboard の版 2 に、ステート ID・検査の再投入回数・終端ステート・原文で保持する部分を足した。

```js
{ version: 3, name, machine, purpose, finish, notes, maxSteps,
  terminals: { done: { id, description }, abort: { id, description } },
  steps: [{ id, kind, title, detail, target, check, checkRetries, outcomes: [{ label, to }], recorded: [], rawTransitions }],
  preserved: { context, config, states, transitions, files: { stateExtras } } }
```

### コンパイル（工程列 → 定義）

作成モードの原則（SKILL.md ステップ 2）をそのまま規則にした:

| 原則 | コンパイルの写し |
|---|---|
| ルーティングをアクションに書かない | 分岐は `transitions` の `condition_rule`（`startswith:last_output:<ラベル>`）。本文にはラベルの一覧だけ |
| 出力形式を強制する | `output_validator: startswith:<ラベル…>`、既定 `OK,FAILED`、`max_retries: 1` |
| 末尾の単一指示 | 全 actions の末尾に固定文 |
| スキルへ移譲するときは名指し | ブラウザ → `` `playwright-cli` スキル ``、Windows → `` `windows-app-automation` スキル ``、スキル工程 → `` `<名前>` スキル `` |
| 成果は check で測る | `check` / `check_retries`。宣言した工程は `equals:check_ok:true` で進む（モデルの OK は材料にしない） |
| 1 ステート 1 成果物 | 1 工程 = 1 ステート。`write` 等の割付は原文から引き継ぐ（画面では編集しない） |

終端は `complete` / `failed`。**どこからも行かない終端は書かない**（既存の定義のステート集合を変えない）。

### 読み戻し（定義 → 工程列）

- `maker.json` があればそれを正とする（往復が正確）。
- 無ければ YAML から起こす: `initial_state` から主経路をたどって順を決め、本文の先頭からスキルの名指し・
  対象・argv を読んで種類と対象を決め、定型（案内・守ること・出力形式・末尾指示）を外して「内容」にする。
- 遷移は `condition_rule` の `startswith/equals:<key>:<ラベル>` を判断に写す。**1 つでも表せない遷移
  （自然言語条件・無条件・未知の行き先）がある工程は、遷移をすべて原文で持つ**（`rawTransitions`）。半分だけ
  生成すると、既定の OK/FAILED が原文の遷移と混ざって意味が変わるため。その工程の `output_validator` も原文のまま。
- 画面で表せないステート（ワイルドカードの行き先など）・`context`・`config` の他のキー・ステートの余分なキー
  （`output_key` / `write` / `max_tool_rounds` …）は `preserved` に持ち、書き戻す。
- スキルの `examples/*.yaml` 全部を読み戻して書き直しても、ステート集合と遷移の数が変わらず `--dry-run` を通る
  （結合テストで固定）。

### 記録

規則は dashboard の `recording.js` と同じ（要素は role と名前・値は `{{key}}`・パスワードは例にも残さない・
goto / ウィンドウの切り替わり / 確定の操作で切る・Windows は次のウィンドウを `winauto wait` で測る）。
違いは呼び方だけ: **PATH の `playwright-cli` / `winauto` を直接呼ぶ**（wsl.exe・tmux・停止ファイルの WSL 越し
読み書きは持たない）。`winauto record` は子プロセスとして起こし、停止ファイルで止め、終了を待って JSONL を読む。
Windows 以外の OS では Windows の記録は「貼り付け」だけにする。

### OS に依らない定義

- パスは `/` 区切り・改行 LF・UTF-8。
- `check` と コマンド工程の argv にシェル記号があれば断る（ハーネスと同じ）。シェル組み込み（`test` / `bash -c` /
  `cmd /c`）・バックスラッシュ・`.exe` は**注意**として画面に出す（エラーにはしない——書けるが動く OS が減る）。
- Windows アプリの工程は Windows 上でしか動かないことを注意に出す（道具の性質で、定義の書き方では避けられない）。
- 実行は `python`（Windows）/ `python3`（他）を先に探して使う。

### 画面 — 既存（agent-dashboard）を下敷きにしない

agent-dashboard の 3 カラム・タブ構成は持ち込まず、ワークフローアプリの型を調べて組み直した。

| 型 | 出どころ | 採ったこと |
|---|---|---|
| 1 列の工程カード。選んだカードだけがその場で開いて設定欄になる | Zapier の Zap エディタ（[Use the editor to build and view your Zaps](https://help.zapier.com/hc/en-us/articles/16722578092429-Use-the-editor-to-build-and-view-your-Zap-workflows)） | 右ペイン・タブを持たない。開くのは常に 1 枚。カードの間の「＋」で挿入 |
| 畳んだカードは「動詞で始まる 1 文の要約」だけ。細部はシェブロンの先 | Apple Shortcuts のパラメータ要約（[Design great actions for Shortcuts](https://developer.apple.com/videos/play/wwdc2021/10283/)） | 「勤怠管理 で 集計を出力」「ブラウザで 申請一覧を読む」のように verb + 名前。補足は種類・対象・検査・記録の件数だけ |
| 分岐は横に広げず縦に積む | Power Automate の新デザイナー（[Classic vs modern designer](https://learn.microsoft.com/en-us/power-automate/classic-vs-modern-designer)） | カードの間に遷移（`OK → 次へ` / `RETRY → 工程 2 へ戻る`）を縦に並べる。戻る遷移は色で目立たせる |

画面は 2 つだけ（一覧と編集）。記録・中身・AI・動かす・設定は `<dialog>` で、常設の領域を増やさない。

**フォルダは登録して使う**（2026-09-04 追加）。見に行くのは登録したフォルダの `.statemachine/` だけで、
画面から届いたパスは `config.isRegistered` を通らなければ main が断る。一覧は左に登録したフォルダ、
右にそのフォルダのステートマシンを並べる。任意のファイルを開く口（旧 `workflow:choose`）と、生パスを
開く口（旧 `shell:openPath`）は落とした——入口が 1 つなら、触る範囲も 1 つで済む。

**画面に出す言葉に内部の用語を入れない**（2026-09-04 追加）。YAML の項目名・コマンドの綴り・ステートの
呼び名は、人が読む言葉に直してから出す（対応表は README）。これは好みではなく**壊れ方の予防**でもある
——`--dry-run` や `output_validator` が画面に出ていると、読む人はそれを打つ場所や書き換える場所を探す。
検査は 2 つ: 画面の原文を見る静的検査（`test/app.test.js`）と、実機で描画された文字を見る煙試験。

**カスタマイズ**: 色・余白・幅・文字はすべて `:root` の CSS 変数。2 段で上書きする。

1. `theme.json`（設定画面）… アクセント色・密度（余白）・文字サイズ・種類ごとの色。main が変数へ変換して当てる。
2. `custom.css`（userData。設定画面から開く）… 人が自由に書く CSS。雛形に変数の名前を載せる。

どちらも userData に置き、定義（`.statemachine/`）には混ぜない。

## 検討した案

| アプローチ | コスト | リスク | 推奨 |
|---|---|---|---|
| **A. 決定的コンパイラで YAML を書き、スキルの engine で検証する**（採用） | 中 | 中（書式の追随が要る → スキルの examples を結合テストで固定） | ★★★ |
| B. dashboard と同じく指示文だけ作り、AI の作成モードに書かせる | 低 | 高（既存の編集ができない。単体で動く成果物に AI の起動が要る） | ★☆☆ |
| C. dashboard の手順ビルダーを別ウィンドウで起動する薄い殻 | 低 | 高（dashboard の設定・登録フォルダ・エージェント起動系に依存し「単体」にならない） | ★☆☆ |

## 非目標

- 並列工程（Fan-out）・Saga 等のパターン。必要なら AI 補完（作成モード）に任せる。
- 定期実行・履歴・通知（dashboard の領分）。
- 記録の再生（再現は生成した定義を既存の実行経路で回す）。

## 検証

```bash
cd tools/statemachine-maker && npm test        # 往復・記録・保存・結合・実機の起動
xvfb-run -a npm run smoke                      # 実機の起動だけ（表示先の無い Linux）
```

CI に `statemachine-maker (npm test)` を足した（python + PyYAML を入れて結合テストを走らせる）。

**実機を起こす検査を入れた理由（事故の記録）**: 初版は画面の確認をブラウザに `window.api` を
差し替えて行い、Electron を起動しなかった。そのため `renderer.js` の `const api = window.api;` が
`contextBridge` の置く **再定義できない** `window.api` と衝突し、スクリプトが 1 行も実行されずに
**画面が真っ白**になる事故をすり抜けた（CSS だけ効くので静的なヘッダーだけが残り、コンソールにも
自分のログが出ないので原因が見えにくい）。代入で作った `window.api` は configurable なので、
ブラウザでの確認では**構造的に再現できない**——だから押さえは 2 つに分けた。

| 検査 | 見るもの | 走る場所 |
|---|---|---|
| `test/preload-contract.test.js` | preload が公開する名前を renderer がトップレベルで宣言していないこと | いつでも（CI 含む） |
| `test/electron-smoke.test.js` | Electron を実際に起動し、一覧・工程・カードの開閉が描画されること | electron のバイナリ・表示先・playwright があるとき（CI では skip） |

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-04 |
| 採用案 | 決定的コンパイラで定義を書き、スキルの engine で検証する単体アプリ |
| 却下案 | 指示文だけ作って AI に書かせる（既存の編集・単体成果物の要件を満たさない）、dashboard の殻 |
| 主な理由 | 既存の編集と単体で動く成果物の 2 要件が「YAML を書く」を要求する。書式の正典はスキルに残し、検証で守る |
| トレードオフ | スキルの書式が変わったら追随が要る（examples の結合テストで検知）。自然言語条件の遷移は画面で編集できず原文のまま |
| 再評価条件 | スキーマの変更が続く場合（scaffold への直接指定へ倒す）、自然言語条件を画面で編集したい需要が出た場合 |
