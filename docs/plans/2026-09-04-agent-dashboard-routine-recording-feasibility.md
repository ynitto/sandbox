# 定型業務の作成に人の操作の記録を使う — 検証結果と設計

> 作成 2026-09-04 ／ 対象: `tools/agent-dashboard`（cowork 制御面）・`tools/winauto`
>
> 問い: **手順ビルダーで playwright-cli / winauto を使う工程を書く手順が分かりにくい。人が操作したのを
> キャプチャして、AI が補完し、拡張性・汎用性を加えてステートマシンのステップにできるか。**
>
> 結論: **できる。ブラウザも Windows アプリも記録から工程を起こせる。**
> 前提となる足場（記録の道具・決定的な変換・作成モードの AI 補完・ステートマシンの `check`）は
> ブラウザ側にはすべて既にあり、欠けていたのは「記録を工程列へ写す段」だけだった。Windows 側は
> 記録そのものが無かったので `winauto record` を足した。実装は変換（`src/features/cowork/main/
> recording.js`）と記録（`tools/winauto/winauto.py`）で、テストを同時に入れている。

## 1. 何が分かりにくかったか

[定型手順ビルダー](./2026-09-03-agent-dashboard-routine-procedure-builder-design.md)は工程の
種類（ブラウザ / Windows アプリ / スキル / コマンド / AI）を並べる入口を作ったが、画面操作の工程の
「内容」欄は**人が文章で書く**。ここで書く人は次を同時に考えることになる。

- どの要素をどう指すか（`getByRole('button', { name: 'ログイン' })` / `auto_id:=btnExport`）。
  snapshot や `winauto tree` を自分で取って確かめないと書けない。
- 操作の粒度（どこまでを 1 工程にするか、どこに `check` を置くか）。
- 毎回変わる値（対象月・ユーザー名）をどう `{{key}}` にするか。

つまり**手順書を書くために自動化の道具の使い方を先に覚える**必要があった。人が「やって見せる」ことが
できれば、要素の指し方と操作の粒度は記録から機械的に決まり、残るのは値の汎用化と例外の扱いだけになる。

## 2. 検証で確かめた事実

### 2.1 ブラウザ — playwright-cli は記録機能を持っている（実測）

`@playwright/cli` 0.1.19（`install.py` が入れる `playwright-cli`）を実際に入れて確かめた。

| 事実 | 出典 |
|---|---|
| `recording-start` / `recording-stop` がある。「人がブラウザでやった操作を記録し、停止時に Playwright コードとして印字する」 | README「DevTools」節、同梱 SKILL.md、`help.json` |
| 印字の形は `` ```js `` フェンスの中の 1 操作 1 行。`await page.getByRole('textbox', { name: 'ユーザー名' }).fill('taro');` | 実測（下記） |
| 要素は **role と名前**（`getByRole` / `getByLabel`）で書かれ、snapshot の ref（`e15`）や座標は出ない | 実測 |
| `--json` で `{ "result": "…" }` として同じ本文が取れる。`generate-locator <ref>` で ref からロケータ式へ戻せる | 実測 |
| CLI 自身の操作（`playwright-cli click e5`）も同じ形のコードを「Ran Playwright code」として印字する | 同梱 `references/test-generation.md` |
| `attach --cdp=chrome` / `--extension=chrome` で**人が普段使っている Chrome/Edge に接続**できる（ログイン状態を持ったまま記録できる） | README「Open parameters」 |
| `click "getByRole('button', { name: 'Submit' })"` のように、**ロケータ式を target にそのまま渡して操作できる**（記録の行が再現の綴りになる） | README「Targeting elements」 |

実測（ローカル HTTP のフォームで、記録開始 → 入力・選択・チェック・ボタン・リンク → 停止）:

```
Recording stopped. Recorded actions:

```js
await page.getByRole('textbox', { name: 'ユーザー名' }).click();
await page.getByRole('textbox', { name: 'ユーザー名' }).fill('taro');
await page.getByRole('textbox', { name: '対象月' }).click();
await page.getByRole('textbox', { name: '対象月' }).fill('2026-09');
await page.getByLabel('種別 通常緊急').selectOption('緊急');
await page.getByRole('checkbox', { name: '同意' }).check();
await page.getByRole('button', { name: 'ログイン' }).click();
await page.getByRole('button', { name: 'ログイン' }).press('Enter');
await page.getByRole('button', { name: 'ログイン' }).click();
await page.getByRole('link', { name: '次へ' }).click();
```
```

記録の癖も見えた。**そのまま定義にすると脆い箇所が 3 種類ある**（これが「AI が補完する」対象）:

1. **人の癖の重複** — 入力欄を押してから入力（`click` → `fill`）、ボタンを押してから Enter、同じボタンの連打。
2. **脆いロケータ** — `getByLabel('種別 通常緊急')` のように、ラベルに選択肢の文字列が混ざる。
3. **待機・確認が無い** — 記録は「うまく行った 1 回」で、要素が出るまでの待機・結果の読み取り・想定外の画面の扱いを含まない。

### 2.2 Windows アプリ — 記録は無かったので足した（`winauto record`）

調査時点の `tools/winauto/winauto.py`（1.1.0）の副命令は `doctor / apps / launch / tree / inspect /
click / type / keys / get-text / screenshot / wait / codegen / run` で、**記録（record）に当たるものは
無かった**。`codegen` は「アプリを起動して要素ツリーをコメントに入れた Python の雛形を書く」だけで、
操作は拾わない。

必要な材料は揃っていたので、`winauto record`（1.2.0）として足した（§5）:

| 要るもの | 今あるもの |
|---|---|
| 要素の名前・auto_id・種類・矩形 | `element_to_dict()`（JSON。`tree --output json` が使う） |
| どのウィンドウが前面か | `Desktop(backend).windows()` / `app.top_window()` |
| イベントの購読 | pywinauto が依存する `comtypes` の UI Automation（`IUIAutomation.AddAutomationEventHandler` — Invoke / 値の変更 / 選択 / トグル / フォーカスの変化） |
| 出力の契約 | **無かった** → 本設計の JSONL（§3.2）で置いた |

`winauto record --app <名前> --output events.jsonl` は、UIA のイベント（Invoke / Value の変化 /
選択 / トグル / ウィンドウの出現 / フォーカスの変化）をデスクトップ全体で購読し、**発生元の PID が
対象アプリのものだけ**を JSONL で書き出す。詳細と決めごとは §5.1。

### 2.3 ステートマシン側 — 補完の受け皿は既にある

- 作成モード（statemachine-use）は「手順の指示文 → `.statemachine/<名前>/` の YAML + actions」を
  書く。分解原則（1 ステート 1 成果物・出力契約・分岐は遷移・移譲先スキルの名指し・`check`）は
  SKILL.md に書かれており、指示文に載せた「記録した操作」と案内をそのまま読める。
- `check`（ハーネスが実行する検査コマンドの終了コード）と `condition_rule` で決定的な遷移が
  書ける（[決定的検査設計](../designs/statemachine-deterministic-check-design.md)）。
  Windows は `winauto wait name:=<画面> --app <アプリ>` が argv で測れる確認になる。
- dashboard の入口は `cowork:generateStateMachine` の 1 本で、手順ビルダーの工程列は main が
  指示文へ決定的に変換する。**記録も同じ工程列に写せば、それより先は何も変えなくてよい。**

## 3. 採用設計 — 「記録 → 工程列」の段を足す

```
人が操作する ──記録──▶ 記録（コード行 / JSONL）──決定的に変換──▶ 工程列（procedure v2）
                                                                    │ 人が画面で直す
                                                                    ▼
                                                     指示文（記録した操作 + 汎用化の案内）
                                                                    │ cowork:generateStateMachine（既存）
                                                                    ▼
                                            作成モードの AI が待機・確認・分岐を補って YAML を書く
```

役割の境界を 3 つに分ける。**機械で決められることは推測させず、推測が要ることは AI に任せ、
決めるのは人**（C4）。

| 段 | 誰が | 何を |
|---|---|---|
| 記録 | 道具（playwright-cli / winauto） | 人の操作を要素の role と名前つきで残す |
| 変換 | dashboard main（`recording.js`）— 決定的 | 癖の除去・工程の区切り・値の `{{key}}` 化・秘密の除去・確認コマンドの候補 |
| 補完 | 作成モードの AI（statemachine-use） | 各操作の前の待機、確定の後の読み取り、脆いロケータの言い換え、想定外の画面での FAILED、`check` / 遷移 |
| 確定 | 人（手順ビルダー） | パラメータ名・工程の名前・判断の分岐・注意事項を直してから作成を開始する |

### 3.1 変換の規則（`src/features/cowork/main/recording.js`）

- **要素は role と名前で持つ。** Playwright のロケータ式（`getByRole(...)`）と winauto の
  セレクタ（`auto_id:=` を優先、無ければ `name:=` + `control:=`）。ref（`e15`）は正規化で断る
  （1 回の snapshot 限りの番号で、再現時には別の番号になる）。
- **癖を落とす。** 連続した同じ操作、`fill` 直前の同じ要素への `click`、`click` 直後の同じボタンへの Enter。
- **工程の区切りは 3 か所で切る。** ページ遷移（`goto`）／ウィンドウの切り替わり（`window` / `launch`）
  ／確定の操作（ボタン・リンク・メニュー・タブへの click、Enter）。1 工程 = 入力の束 + 確定で、
  確定の直後が `check` を置ける場所になる（作成モードの原則「検査を置ける単位で割る」に合わせる）。
- **値は `{{key}}` にする。** `fill` / `type` の値だけ（人が毎回変える入力）。`select` / `check` は
  選択肢＝定数のまま残す。記録時の値は「例」として残し、パスワードらしい欄（`パスワード` /
  `password` / `token` / `暗証` …）は例にも残さない。key はラベルが ASCII ならそれ、そうでなければ
  `input_N`（人が画面で直す）。
- **確認コマンドの候補。** Windows は `winauto wait name:=<画面> --app <アプリ>`。測るのは
  **その工程がもたらした変化**なので、見るのは「次の工程が始まったときのウィンドウ」である
  ——工程の最中に既に出ていたウィンドウを待っても、押す前から真なので何も検知しない
  （検知装置が別のものを測るのは検知が無いことより悪い。[決定的検査設計](../designs/statemachine-deterministic-check-design.md) §3.2）。
  次が無い最後の工程には置かない。ブラウザは argv で測れる確認が無いので常に空にし、作成モードに
  snapshot / テキストの読み取りで確かめさせる。
- **推測しない。** 待機・分岐・例外は変換では足さず、指示文の案内（`RECORDED_GUIDANCE`）で作成モードへ渡す。

### 3.2 記録の入力形式

| 種類 | 形 | 出所 |
|---|---|---|
| ブラウザ | `recording-stop` の出力（`` ```js `` フェンス付き全文でも、`await page.…` の行だけでもよい） | playwright-cli（既存） |
| Windows アプリ | JSONL。1 行 1 イベント: `{"event":"invoke"\|"value"\|"select"\|"toggle"\|"keys"\|"window"\|"launch", "app", "window", "control_type", "name", "auto_id", "value"}` | `winauto record`（§5.1。契約は `recording.js` の `WINAUTO_EVENT_KINDS`） |

### 3.3 工程列（正規形）の変更 — version 2

工程に任意の `recorded[]` を持てるようにし、版を 2 に上げる（版 1 の項目はそのまま読める）。

```js
{ kind: 'browser', title, detail, target, check, outcomes,
  recorded: [{ op: 'fill', target: "getByRole('textbox', { name: '対象月' })", role: 'textbox',
               label: '対象月', value: '{{input_1}}', example: '2026-09' }, …] }
```

- `recorded` を持てるのは種別カタログで `recordable: true` の種類（ブラウザ / Windows アプリ）だけ。
- 指示文の工程節に「記録した操作」を `<操作> <要素> [値]` の形で載せる（`fill getByRole(...) "{{input_1}}"` /
  `click "auto_id:=btnExport"`）。**これは「何が起きたか」であって、そのまま打てる argv ではない。**
  道具のコマンドへの読み替えは「使う道具」節の対応表で示し、**無いコマンドは無いと書く**
  （`select` に当たる winauto のコマンドは無い。あるふりをすると、動かない argv がそのまま定義に入る）。
  同じ節に汎用化の案内（記録に無い操作を足さない・ref を書かない・待機と読み取りを足す・
  例を既定値にしない・脆いロケータは言い換えてよい）を、記録を持つ手順のときだけ載せる。
- 記録の `{{key}}` は入力パラメータとして拾う（`parameterKeys` が `recorded[].value` も読む）。

### 3.4 画面と入口

- 手順ビルダーに「操作を記録する」を足す。ブラウザは URL を入れて **記録を開始**（`playwright-cli -s=agent-dashboard-record open --headed <url>` → `recording-start`）→ 人が操作 →
  **記録を終了して工程に起こす**（`recording-stop` → 変換 → `close`）。Windows アプリと、別の端末で
  取った記録は**貼り付け**で受ける。
- 起こした工程は一覧の末尾に足すだけで、人が名前・内容・パラメータ名・判断を直して「作成を開始」する。
  工程カードは記録した操作を畳んで見せ、「記録を外す」で文章だけの工程に戻せる。
- IPC は `cowork:procedureRecording`（start / stop / import）の 1 チャネルだけ。**作成の入口は増やさない**
  （`cowork:generateStateMachine` の 1 本のまま）。記録専用のセッション名で、作業のセッションと混ぜない。
- 実行側と同じ側で呼ぶ（win32 は wsl.exe 経由）。cwd は登録済みフォルダのときだけそこへ寄せる。

## 4. 検討した案

| アプローチ | コスト | リスク | 推奨 |
|---|---|---|---|
| **A. 記録を工程列へ決定的に写し、補完は作成モードの AI に任せる**（採用） | 低（変換 1 モジュール + 画面 1 節） | 低。既存の入口・実行系を変えない | ★★★ |
| B. 記録から dashboard 内蔵 AI が工程列の下書きを作る（セッション蒸留 `distillSession` と同じ形） | 中 | 中。決定的に写せるものを推測させる（工程の区切り・値の扱いが毎回揺れる）。LLM 呼び出しが 1 回増える | ★★☆（A の上に「工程の名前と説明を整える」用途で後から足せる） |
| C. 記録をそのまま再生する RPA ランナーを dashboard に持つ | 高 | 高。既存の実行系（`check` / 定期実行 / 履歴）を捨てて作り直す。前設計で却下済み | ★☆☆ |
| D. 記録のコードをそのまま `run-code --filename` で流す工程にする | 低 | 高。ref・待機・値の汎用化が無いまま固定され、画面が変わると丸ごと壊れる。人が直す余地が無い | ★☆☆ |

A を採る。B は A の後段としてなら意味がある（説明文の整形は推測でよい）。D は「汎用性を加える」の
要件を満たさない。

## 5. 残る作業と限界

| 項目 | 状態 | 備考 |
|---|---|---|
| ブラウザの記録 → 工程 → 指示文 | **実装済み**（本 PR） | playwright-cli 0.1.19 で実測 |
| Windows アプリの記録の受け口（JSONL → 工程） | **実装済み**（本 PR） | 契約は `WINAUTO_EVENT_KINDS` |
| `winauto record` コマンド | **実装済み**（§5.1） | comtypes の UIA イベント購読 → JSONL。`LOCKED_COMMANDS` に入れない（読み取り専用・数分動く）。`doctor` に `uia_events` の検査を足した |
| dashboard から `record` を起こす | **実装済み**（§5.2） | 別ウィンドウ（tmux）で走らせ、停止ファイルで止める |
| `winauto select` | **実装済み** | 一覧・コンボ・タブから選ぶ副命令。`click` は畳まれたコンボを開くだけ、`type` は編集できないコンボで例外になるので、どちらでも代われなかった。記録の `select` を再現する口でもある |
| 画面操作の道具をどちら側で呼ぶか | **実装済み**（§5.3） | win32 は Windows 側の実体を先に探し、無ければ WSL 経由へ落ちる。AI の解釈・拡張の道具は WSL 側のまま |
| ブラウザの記録の実行環境 | **制約あり（手当て済み）** | Windows 側に `playwright-cli` が入っていればそちらが選ばれ、普段使いのブラウザが出る。WSL 側の実体しか無い端末では WSL に画面（WSLg）が要るので、**落ちた理由ごとに次の一手を出す**（`browserOpenHint`: 画面が無い / CLI が無い / ブラウザの実体が無い）。逃げ道は「別の端末で取った記録の貼り付け」で、これは常に使える |
| 作成モードが記録を読み違える | 未計測 | 指示文の「記録した操作」節と案内で担保する。事例が出たら案内の文言を足す（YAML の直接指定へは倒さない） |
| 判断の分岐（AI の処理の工程） | 記録からは出ない | 記録は 1 本道。分岐は人が「AI の処理」工程を足し、判断欄に書く（従来どおり） |

### 5.1 `winauto record` の決めごと

```bash
winauto record --app 勤怠管理 --output events.jsonl   # 操作して Ctrl+C で止める
winauto record --app 1234 --duration 120              # 秒で止める / --max-events N でも止まる
```

| 決めごと | 理由 |
|---|---|
| **キーボードのフックは取らない** | 打鍵を拾うには低レベルフック（`WH_KEYBOARD_LL`）が要る。あれはデスクトップ全体のキーロガーで、対象アプリ以外へ打ったパスワードまで JSONL に落ちる——その JSONL は人がそのまま AI へ貼る。入力欄の文字は Value の変化（`value`）、キーボードで開いたメニューは `invoke` で拾えるので、残る穴は「UI に対応物の無いショートカット」だけ。読み手は `keys` を受けるので手で足せる |
| **`--app` は必須**（対象 PID 以外は捨てる） | 購読はデスクトップ全体に掛けるしかないが、書き出す前に発生元の PID で捨てる。録画のつもりで無関係のアプリの操作を残さない |
| **ロックを取らない** | 読むだけで入力を奪わない。人が数分操作する間デスクトップロックを占有すると他の発行が全部止まる |
| **打鍵ごとの Value 変化は畳む** | `"2" "20" "202" …` を全部書くと読めない。同じ要素のぶんを溜めて最後の 1 件だけ書き、別の要素や別の種類が来たら先に出す（人がやった順は崩さない） |
| **状態の二重通知は落とす** | ComboBox の選択は SelectionItem と Value の両方から来る。直前とまったく同じ行だけ落とす（`invoke` は落とさない——同じボタンを 2 回押すのは人の意図でありうる） |
| **出力の形は `RecordSink` 1 か所** | Windows も pywinauto も要らない層に閉じ込めてあるので、畳み・重複除去・上限を単体で検査できる |

イベント種別の契約は両端のテストで固定する（`RECORD_EVENT_KINDS` ⊆ 読み手の
`WINAUTO_EVENT_KINDS`、差は `keys` だけ）。`doctor` の `uia_events` は購読可否を見るが
**warn 止まり**である——購読できなくても `click` / `type` / `tree` は動くので、終了コードを
1 にする（＝橋が壊れている）意味にはしない。

### 5.3 どちら側の実体を呼ぶか — 画面操作の道具は Windows 側

`playwright-cli` と `winauto` は **Windows のデスクトップを触る道具**である。AI の解釈・拡張
（エージェント CLI・agent-loop・statemachine-use のハーネス）が WSL 側にあるのとは立場が逆で、
当初はすべてを `wsl.exe` 経由で呼んでいたため遠回りになっていた。

| 道具 | WSL 経由で呼ぶと |
|---|---|
| `winauto` | WSL のラッパーは結局 Windows の `python.exe` を exec する。Electron → `wsl.exe` → bash → `python.exe` と 2 回跨ぐぶん、パス変換とロケールの取り違えが増える。**Windows にしか入れていない端末では WSL 側に実体が無く「未準備」に見えていた** |
| `playwright-cli` | WSL の中でブラウザを開こうとする。WSLg の無い環境では見える形で開けず、人の操作を記録できない。Windows 側なら普段使いのブラウザがそのまま出る |

そこで **win32 では Windows 側の実体を先に探し、見つかればそれを直接呼ぶ**
（`src/features/cowork/main/screen-tools.js`）。見つからなければ従来どおり `wsl.exe` 経由へ
落ちるので、片側にしか入れていない端末でも動き続ける。**AI の解釈・拡張の道具はこの扱いを
受けない**——あちらは WSL 側が正しい（`cliSpawnSpec` の既定は今までどおり `wsl.exe` 経由で、
`options.native` を立てたときだけネイティブへ倒れる）。

決めごとが 3 つ連動する。**別々に決めると必ず食い違う**ので、1 か所（`screenToolKit`）で組む。

| 連動するもの | ネイティブ（Windows 側） | WSL 側 |
|---|---|---|
| 実体の呼び方 | `cmd /d /s /c "…"`（`.bat` / `.cmd` は直接 spawn できない） | `wsl.exe -e sh -lc "…"` |
| 一時ファイルの綴り | `%TEMP%\agent-dashboard\…` | `/tmp/agent-dashboard/…` |
| 読み書き | main プロセスから直接 `fs` | 実行と同じ `wsl.exe` 越し（`cat` / `touch` / `rm`） |
| 記録の窓 | Windows のコンソール（一時 `.cmd`） | WSL の端末 + tmux（一時 `.sh`） |

Windows 側の探索は **PATHEXT を補う**。npm のグローバル導入は `playwright-cli.cmd`、winauto の
Windows インストーラは `winauto.bat` を置くので、拡張子なしの名前だけを見る POSIX 流の探索
では**どちらも見つからない**。優先順は PATHEXT のままにする（Windows 自身がその順で解決するので、
ここで独自の順を作らない）。

「道具を確認」は**どちら側で見つけたか**まで出す。同じ「利用可能」でも意味が違うし、
「未準備」とだけ言われても、どちらへ入れればよいのか分からない。

### 5.2 dashboard から記録を起こす

ブラウザと Windows で**止め方が違う**。ブラウザは `recording-stop` を呼べば止まるが、
`winauto record` は人が操作している間ずっと走る別プロセスで、止める合図を送る先がいる。

信号（SIGINT）は使えない。win32 の dashboard → `wsl.exe` → ラッパー → Windows Python と
挟まっていて、途中で落ちるからである。代わりに**停止ファイル**を使う
（`winauto record --stop-file <PATH>`）。ファイルなら WSL 側からも Windows 側からも同じ実体が
見え、挟まっているものを 1 つも増やさない。

```
開始 → runCommandWindow で `winauto record --app X --output <out> --stop-file <stop>`
       （別ウィンドウの tmux。人はそこで進行を見られ、Ctrl+C でも止められる）
終了 → <stop> を touch → recorder が気づいて flush して終了
       → <out> を 2 回続けて同じ中身になるまで読む（止めた直後は最後の数行がまだ出ていない）
       → 工程列へ変換 → 一時ファイルを消す
```

一時ファイルの綴りは **main が決めて覚える**。画面から受け取ったパスで読み書きすると、
画面が指した任意の場所を触れることになる（画面は信頼しない、と同じ理由）。パスは
**WSL 側の POSIX パス**で持ち、ラッパーが `--output` / `--stop-file` を Windows パスへ変換する
——読み戻しも同じ `wsl.exe` 越しなので、両側が同じ実体を見る。

## 6. 非目標

- 画面が workflow.yaml を書くこと・読み戻すこと（書式の正典はスキル。C7）。
- 記録の再生を dashboard が行うこと（再現は作成された定義を既存の実行経路で回す）。
- 動画・スクリーンショットの記録（`video-start` / `tracing-start` はあるが、工程列の材料にはコード行で足りる）。

## 7. 検証

```bash
cd tools/agent-dashboard
node test/routine-recording.test.js     # 記録 → 工程列 → 指示文（実測の出力を固定）
node test/routine-procedure.test.js     # 手順ビルダー（版 2 で従来の項目も読める）
npm run lint && npm test

cd ../..
python -m unittest discover -s tools/winauto/tests   # record の出力の形・畳み・契約
```

playwright-cli の記録の実測（再現手順）:

```bash
npm i @playwright/cli@0.1.19
printf '{ "browser": { "browserName": "chromium", "launchOptions": { "headless": true, "chromiumSandbox": false } } }' > .playwright/cli.config.json
playwright-cli open http://127.0.0.1:8765/page.html
playwright-cli recording-start
# （人が操作する。検証では run-code で page.mouse / page.keyboard の実入力を送った）
playwright-cli recording-stop            # → 上記の ```js … ``` が印字される
```

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-04 |
| 採用案 | 記録を工程列へ決定的に写し、待機・確認・分岐の補完は作成モードの AI に任せる。作成の入口・実行系は変えない |
| 却下案 | dashboard 内蔵 AI による工程の推測（後段としては可）、RPA ランナー、記録コードの直接実行 |
| 主な理由 | playwright-cli が role/名前つきの記録を既に持ち、statemachine-use の作成モードと `check` が補完の受け皿になる。欠けていたのは写す段だけ |
| 前設計との関係 | [定型手順ビルダー設計](./2026-09-03-agent-dashboard-routine-procedure-builder-design.md)の非目標「画面操作の記録（レコーダー）」を再評価した。当時の理由は「要素の特定はエージェントが偵察する」だったが、記録は偵察を**置き換えるのではなく前段に置く**（偵察＝待機と読み取りは残る） |
| トレードオフ | dashboard から起こせるのはブラウザの記録だけで、Windows は貼り付け（`winauto record` は端末で回す）。ブラウザの記録は環境（WSLg / CDP 接続）に依る |
| 再評価条件 | 作成モードが記録を読み違える事例が続く（案 B を後段に足す）、`winauto record` の UIA イベントが取れないアプリが多い（ポーリング差分方式へ） |
