# 記録した操作を「やりたいこと」で広げる — statemachine-maker 記録拡張の検討

**状態: 実装済み（2026-09-06）**
対象: `tools/statemachine-maker`（agent-app には vendor 経由で載る）
前提の設計: [記録の実現可能性](2026-09-04-agent-dashboard-routine-recording-feasibility.md) §3、[maker 設計](2026-09-04-statemachine-maker-design.md) §記録、[AI 支援設計](2026-09-05-statemachine-maker-ai-assistance-design.md)

## 結論

記録は「人が 1 回通った経路」しか残らない。繰り返し・読み取り・分岐・失敗時の扱いは、
**記録に足すのではなく、工程に付ける修飾として UI に口を作る**。修飾は 4 種に絞る
（繰り返す／読み取る／確認する／失敗したら）。本文への書き下ろしは機械が決まった文で行い、
候補の提案だけを既存の AI 見直しに任せる。記録を 3 回取り直す必要はなくなり、
「全文を取る」は要素を選んで「読み取る」を付ければ済む。

Qiita の例は、記録 1 回（トレンドを開く → 1 件目を開く → 本文の要素を「読み取る」で選ぶ）に
「一覧を繰り返す: 最新 3 件・詳細に入って戻る」「失敗した件は飛ばす・0 件なら FAILED」を
付けるだけで工程 2 つになる（§5 に本文の例）。

## 1. 何が困るか

依頼に挙がった 3 つは、どれも「記録 = 経路 1 本」という性質から出ている。

| 困りごと | 記録に何が無いか |
|---|---|
| 3 件取りたいのに 3 回操作したくない | 「同じ形の要素を N 回」という繰り返しの宣言 |
| 全文を取るという操作が無い | 読み取りは操作でなく「要素とほしい形」の宣言 |
| 内容次第で分岐する・失敗する | 想定外の画面をどう扱うかの宣言 |

現行の変換（`recording.js`）は「推測しない」を規則にしており、これらは指示文の案内で
作成モードの AI に丸投げしている。動くかどうかは AI の解釈次第で、人が直す口が無い。

## 2. 一般的なアプリの実現例

| 製品 | 記録の後にどう広げるか |
|---|---|
| Playwright codegen | 記録は直線。条件・ループ・パラメータ化・独自の検証は手で編集する前提。記録できる検証は表示・テキスト・値の 3 種だけ |
| Power Automate Desktop | 「Web ページからデータを抽出」は **要素を 2 件示すと一覧全件に汎化**する。ページ送りは RowsCount でループを組む。抽出はレコーダーとは別のアクション |
| UiPath | Table Extraction ウィザードで表を選ぶ。失敗時は Retry Scope / Try Catch / Continue on error を工程に**巻く** |
| Octoparse | Auto-detect が「一覧・次ページ・もっと見る」を自動検出し、Tips パネルで「各項目をクリック」を選ぶと Loop Item を生成。詳細から一覧へ**戻る手段**（戻るボタンか一覧 URL を再度開く）が要る |
| Axiom.ai | 1 回操作して記録し、編集でループ・条件・データ入力を足す |
| Skyvern | ブロック型。Navigation / Extraction / Validation / Loop / Wait。Extraction は移動しない |
| Stagehand | act / extract（スキーマ付き）/ observe。observe の結果をキャッシュして決定的に再生し、壊れたら AI で探し直す |
| browser-use workflow-use | 記録 → 入力欄を変数化 → 決定的に実行。工程が失敗したらエージェントに落とし、ワークフローを書き換える |

共通点は 4 つ。

1. **記録は経路 1 本。** 繰り返し・条件は記録に入れず、記録の後に工程へ付ける。
2. **読み取りは操作ではなく宣言。** 「この要素」と「ほしい形（一覧・表・全文）」を選ばせる。2 件示すと一覧に汎化するのが定石。
3. **繰り返し・ページ送り・詳細に入って戻る は定型。** 自由記述でなく選択肢で与える。
4. **失敗は工程の修飾。** 再試行・飛ばす・中止・AI に任せる、を工程に巻く。

Stagehand と workflow-use は「決まった所は固定し、判断は AI」という混成で、これは
本アプリの現状（記録した操作を手がかりに `playwright-cli` スキルの AI が実行する）と同じ骨格である。
足りないのは上の 4 つの口が画面に無いことだけで、実行系を変える必要はない。

出典: [Playwright codegen](https://playwright.dev/docs/codegen)、[PAD リンク一覧のクリック](https://learn.microsoft.com/en-us/power-automate/desktop-flows/how-to/click-elements-list-links)、[UiPath Table Extraction](https://docs.uipath.com/activities/other/latest/ui-automation/table-extraction)、[UiPath Retry Scope](https://docs.uipath.com/activities/docs/retry-scope)、[Octoparse Auto-detect](https://helpcenter.octoparse.com/en/articles/6470911-what-is-auto-detect-and-how-to-use-it)、[Octoparse 各リンクを開く](https://helpcenter.octoparse.com/en/articles/6470996-click-each-link-in-a-list-and-scrape-data-from-new-pages)、[Skyvern workflow blocks](https://docs.skyvern.com/workflows/workflow-blocks-details)、[Stagehand caching](https://docs.stagehand.dev/examples/caching)、[workflow-use](https://github.com/browser-use/workflow-use)

## 3. 採用設計 — 3 つの機能

```
記録（1 回）──変換──▶ 工程列 ──人が「修飾」を付ける──▶ コンパイルが決まった文を書く ──▶ 実行
      │ 記録中に「読み取る」を挿せる                 ▲
      └────────────────────────────────── AI 見直しが修飾の候補を提案
```

### F1. 記録中に「これを読み取る」を挿す

記録ダイアログの記録中の状態に **「いま見えている要素を読み取る」** ボタンを足す。
押すと同じセッションで `playwright-cli snapshot` を取り、要素の一覧（role と名前）を出して人に選ばせる。
選んだ ref は `playwright-cli locator <ref>` でロケータ式に直し（ref は再現時に変わるので残さない）、
記録の列に `extract` 操作として挿す。取りたい形は 3 択: **全文 / 表 / 一覧の各項目**。

- 「全文を取る」がここで表現できる。記事本文なら `article` か `main` を選ぶ。
- 記録を止めずに済む。playwright-cli は recording 中も同じセッションで他のコマンドを受ける（既存の記録開始が `open` の後に `recording-start` を打っている形と同じ）。
- Windows アプリは `winauto tree` で同じことができるが、初版はブラウザだけにする。

### F2. 工程の修飾（4 種）

工程カードの「拡張」から選ぶ。**自由記述ではなく決まった選択肢**にし、コンパイルが本文へ決まった文を書く。

| 修飾 | 選ぶもの | コンパイルが書く文 |
|---|---|---|
| 繰り返す | 対象（記録した要素と同じ形の一覧）・件数（N 件 / 全件 / 次ページも）・詳細に入って戻る（戻る操作 / 一覧 URL を開き直す） | 「同じ形の要素を先頭から N 件、順に次を行う。各件の後は〈戻り方〉で一覧へ戻る」＋出力を JSON 配列で契約 |
| 読み取る | 要素（F1 で選んだもの、または後から snapshot で選ぶ）・形（全文 / 表 / 一覧）・出力名 | 「〈要素〉のテキストを丸ごと取り、`<出力名>` として返す。要約しない・切らない」 |
| 確認する | 期待（要素が見える / テキストを含む / 件数が N 以上） | 「確定の後、〈期待〉を snapshot で確かめる。満たさなければ FAILED」 |
| 失敗したら | 1 件の失敗（飛ばす / 中止）・工程の失敗（再試行 n 回 / 中止 / AI に任せる） | 「1 件で失敗しても残りを続け、失敗した件を末尾に列挙する」「想定外の画面では別の操作を試さず FAILED」（既存の規則を修飾で上書きできる形にする） |

繰り返しの**実装先**は 1 工程の中とする（AI が 1 つの工程の中で N 件回す）。理由:

- 戻り遷移とカウンタ（`context` の変数）でステートを回す形は、カウントの管理を LLM に任せることになり崩れる。
- agent-flow の split / map（`flow-model.js` に既にある）は件数が多いときの正しい口だが、
  3 件のために run を分けるのは重い。件数上限（既定 20）を超えて指定したら「agent-flow の分割へ」と注意を出すだけにする。
- 1 工程なら出力を JSON 配列で契約でき、`check` で件数を機械的に測れる（`python scripts/check_count.py 3` の形）。

### F3. AI 見直しに「一般化」の観点を足す

既存の AI 見直し（findings の区分 consistency / efficiency / error-handling / edge-case）に
**generalization** を足す。記録つきの工程と目的文を渡し、「この工程は繰り返しに見える（同じ形の要素が並ぶ）」
「読み取りが無い」「戻り方が無い」を F2 の修飾の**候補**として返させる。Octoparse の Auto-detect を LLM で
代替する位置づけで、採否は既存の提案 UI（チェックして反映）で人が決める。
決められることを推測させない原則はそのままで、AI は候補を出すだけ、確定は人、書き下ろしは機械。

## 4. データと変換

工程列を version 4 にする（版 3 はそのまま読める）。

```js
step: { …既存,
  recorded: [ …, { op: 'extract', target: "getByRole('article')", role: 'article', label: '', mode: 'text', key: 'body' } ],
  extend: {
    loop:    { over: "getByRole('link', { name: … })", count: 3 | 'all' | 'pages', back: 'history' | 'goto' | '' },
    extract: [{ target, mode: 'text' | 'table' | 'list', key }],
    expect:  { kind: 'visible' | 'text' | 'count', target, value },
    onError: { item: 'skip' | 'abort', step: 'retry' | 'abort' | 'agent', retries: 1 },
  } }
```

- `extend` を持てるのは `recordable` な種類だけ。空なら本文は今と変わらない（既存の定義の書き戻しは不変）。
- `extract` は F1 の挿入でも F2 の追加でも同じ形。`recordedLine` は `extract <ロケータ> --mode text` と書き、
  `recordedHint` に `playwright-cli eval "el => el.innerText" <ref>` への読み替えを載せる（無いコマンドは無いと書く規則どおり、winauto は `get-text`）。
- 変換（`recording.js`）は `extract` を確定操作として扱わない（工程を切らない）。読み取りは確定の**後**に置かれるのが自然なので、直前の工程に属させる。
- `loop.over` の初期値は「その工程で最初にクリックした要素のロケータ」。名前を落として role だけにした形（`getByRole('link')`）を候補にし、人が直す。
- 出力契約: `extract` か `loop` を持つ工程は、第 1 行のラベルの後に JSON を返す形を本文に書く。`output_key` に `extend.extract[].key` を割り付け、後続の工程が `{{last_output}}` で受ける。

## 5. Qiita の例で通す

記録 1 回: `goto https://qiita.com/` → トレンドの 1 件目のリンクを click → 記事が開く →
F1 で `article` を選び「全文」で読み取る → 記録を終える。

変換後の工程: 1「画面を開く」、2「『〈記事名〉』リンクを押す」（recorded に click と extract）。

人が付ける修飾（工程 2）: 繰り返す = 対象 `getByRole('link')`（トレンド一覧の中）・3 件・戻り方「一覧 URL を開き直す」。
読み取る = `article` 全文 → `body`。失敗したら = 1 件は飛ばす・工程は中止。

コンパイルが書く本文（抜粋）:

```
記録した操作: …（既存どおり）
繰り返し: トレンド一覧の同じ形の要素（getByRole('link')）を先頭から 3 件、順に次を行う。
  各件で: リンクを押す → getByRole('article') のテキストを丸ごと読み取る（要約しない・切らない）
  → https://qiita.com/ を開き直して一覧へ戻る。
失敗したら: 1 件で読み取れなくてもその件を飛ばして残りを続け、末尾に飛ばした件を列挙する。
  一覧そのものが出ない・0 件しか取れないときは FAILED。
出力形式: 第 1 行に OK / FAILED。続けて JSON: {"articles":[{"title":…,"url":…,"body":…}]}
```

`check` の候補: `python scripts/check_json_count.py articles 3`（スクリプトは maker が生成ファイルに同梱する）。

## 6. 検討した案

| 案 | コスト | リスク | 推奨 |
|---|---|---|---|
| **A. 工程の修飾（4 種）＋記録中の読み取り挿入＋AI が候補**（採用） | 中（version 4・UI 2 か所・コンパイル文面） | 低。実行系・入口は不変。修飾が空なら今と同じ | ★★★ |
| B. 記録を 2 回取り、差分から一覧を汎化する（PAD の「2 件示す」方式） | 中 | 中。依頼の「1 回で済ませたい」に反する。差分の取り方が揺れる | ★☆☆ |
| C. 修飾を UI に持たず、全部 AI 見直しに任せる | 低 | 高。毎回違う文になり人が直せない。決められることを推測させる | ★☆☆ |
| D. 繰り返しをステートの戻り遷移＋カウンタで組む | 中 | 高。カウント管理を LLM に任せて崩れる（小型モデルで実測済みの失敗族） | ★☆☆ |
| E. 繰り返しを agent-flow の split/map に落とす | 高 | 中。3 件のために run が分かれる。件数が多いときの口として A の後段に残す | ★★☆ |

## 7. 非目標

- 記録の決定的再生（RPA ランナー）。前設計で却下済み。
- 表の列を選ぶ細かい抽出 UI。初版は「表を丸ごと」で、列の選別は後続の AI 工程に任せる。
- Windows アプリの記録中の読み取り挿入（`winauto tree` で同じことはできるが、初版はブラウザだけ）。
- ページ送りの自動検出。`count: 'pages'` は「次ページのリンクを人が選ぶ」形で受ける。

## 8. 検証

- 単体: `recording.js` が `extract` を工程を切らずに直前の工程へ入れる。`model.js` が `extend` を version 3 の定義から読んでも空で通り、書き戻しで差が出ない（既存の `examples/*.yaml` 往復テストに追加）。
- コンパイル: 修飾ごとの文面をゴールデンで固定。`extract` を持つ工程の `output_key` 割付。
- 実機: Qiita のトレンド 3 件で、記録 1 回 → 修飾 → 実行が `check` を通る（`articles.length == 3`）。
- agent-app: vendor 同期後に `app.test` の preload 照合が通る。

## 9. 実装で決めたこと（設計との差分）

- **読み取りは `recorded[]` の `extract` 操作だけに持つ。** 設計の `extend.extract[]` は作らなかった。記録中に挿しても、後から工程で足しても同じ形になり、二重管理が要らない。`extend` は繰り返す／確認する／失敗したら の 3 つ。
- **記録中の読み取りの位置は `recording-stop` → `recording-start` で決める。** 実測で、記録中に `snapshot` が打てること、stop で区切った後に start で再開できること、操作が無い区切りは「No actions were recorded」になることを確かめた。区切りと読み取りを順に積むので、位置を推測しない。
- **要素は snapshot の role と名前から `getByRole(...)` に写す。** `generate-locator` は `getByText('…')` の脆い形を返すことがあったので使わない。
- **`check` の候補スクリプトは生成しない。** 出力の JSON 契約（`items` / `skipped`）を本文に書くところまでにし、件数の機械検査は人が `check` に書く。
- **YAML だけからの読み戻しでは修飾は戻らない**（記録と同じ扱い）。maker.json があれば正確に戻る。

## Decision Record

- 2026-09-06: 検討を起票。修飾は 4 種に絞り、繰り返しの実装先は 1 工程内、件数が多いときだけ agent-flow へ誘導する。
- 2026-09-06: 実装。工程列 version 4（`step.extend`、`recorded[].op = 'extract'`）。IPC に `recording:snapshot` / `recording:extract` を追加。AI 見直しに `generalization` を追加。
