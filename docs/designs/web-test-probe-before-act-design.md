# web-test — 操作の前に対象を確かめる探索（--probe-before-act）設計

> 最終更新: 2026-10-01 ／ 関連: `tools/web-test/src/probe.js`, `src/generate.js`, `src/cli.js`,
> `scripts/compare-explore.js`, `examples/sample-app/editor.html`, `examples/editor.yaml`, `test/probe.test.js`
>
> 発想の出どころ: "Probe to Act: Elevating Browser-Use Agent via Active Visual Probing"（arXiv:2609.33646）。
> 取り入れたのは「状態を変える直前に対象を能動的に確かめる」「確かめた・行った・はっきり残した観察だけを持つ」の 2 点だけで、
> P2A のフレームワーク・モデル・視覚のみの対象登録は入れていない。

## 1. いまの `generate --explore` のデータの流れ（PR #884 時点）

```
conditions（自然文 / -f）
  │
  ├─(--url, --no-snapshot でなければ)─ snapshotPage(): Playwright で 1 回開き body.ariaSnapshot()
  │                                     → pageInfo { title, url, aria }（2 万文字で切る）
  ▼
buildPrompt(): 守ること + format-reference.md + 条件 + 対象 + pageInfo
  │            + (explore) 「画面を操作して確かめる」節: `<playwright-cli -s=<session>> snapshot / click e12 / fill / goto`
  ▼
.web-test/request-XXXX/request-<n>.md（依頼ファイル）
  │
  ├─ openExploreSession(): playwright-cli.json を書き、`playwright-cli -s=web-test-<pid>-<t> open <url>`
  │                        → 環境変数 WEB_TEST_PLAYWRIGHT_CLI にコマンド文字列
  ▼
runAgent(): kiro-cli / copilot（explore は全ツール許可）に「依頼ファイルを読め」
  │   └─ エージェントがシェルから playwright-cli を直に叩く（snapshot → ref → click e12 …）
  ▼
extractYaml(): 最後の ```yaml ブロック
  ▼
normalize(): 書式の検査 ── 誤り → feedback を付けて再依頼（--retries、既定 1）
  ▼
baseUrl を補って -o に保存 → finally で playwright-cli close・作業ディレクトリを消す
```

画像の置き場の約束（run）: `<out>/<日時>/<suite>/<case>/NN-<名前>.png`、`results.json` からは out からの相対パスで指す。

## 2. 状態を変える直前に何を持っていたか（ギャップ）

| 持っていたもの | 欠けていたもの |
|---|---|
| 最後に取った snapshot の ref（e12） | ref は「その snapshot の時点のその要素」。YAML に書く role+name で 1 つに決まるかは誰も確かめていない |
| aria snapshot の役割と名前 | 見えているか・押せるか。隠れた重複（狭い画面用メニュー）・無効なボタンを区別できない |
| エージェントの記憶（会話） | 操作のあとに ref が古くなったか。DOM の作り直し・遷移を検知する手段がない |
| — | 何を確かめて何を押したかの記録。依頼文の「取り消せない操作はしない」以外の歯止めがない |

結果として、YAML に `text: 保存` のような 1 つに決まらない指定が入り、初回の `web-test run` で strict mode violation になる。

## 3. 方針

- 新しいブラウザ自動化の仕組みは足さない。確かめも操作も、既に開いている playwright-cli のセッションに `run-code` で送る。
- エージェントに渡すコマンドを見張り役 `web-test browse` に差し替える（opt-in の `--probe-before-act` のときだけ）。
  playwright-cli と同じ書き方を受け、`probe` / `observe` と `--probe <ID>` を足しただけ。
- 依頼文にも手順を書くが、安全は見張り役が持つ（依頼文に従わない操作は断る）。
- 確かめる対象の書き方はテストケースの対象（`{ role, name, exact, nth }` など）と同じにし、Playwright での解釈も
  `runner.js` の `toLocator` と同じにする。ready になった指定をそのまま YAML に書けば、実行でも 1 つに決まる。
- `casefile.js` の書式は変えない（probe は最終の YAML に要らない）。

## 4. コマンドの分け方（`probe.js` の `classify`）

完全な副作用の分類器は作らない。表で引くだけ:

| 種類 | コマンド | 扱い |
|---|---|---|
| action | click / dblclick / fill / select / check / uncheck / press（Enter・Space・NumpadEnter） | 新しい ready な probe が要る |
| read | snapshot / find / screenshot / console / *-list / *-get / requests … | そのまま通す。probe は古くならない |
| navigation | goto / go-back / go-forward / reload / tab-* | そのまま通す。それまでの probe はすべて古くなる |
| lifecycle | open / close / attach / kill-all … | 断る（ブラウザは web-test が開け閉めする） |
| other | それ以外（eval / run-code / type / hover / press Tab / resize / localstorage-set …） | そのまま通すが、それまでの probe はすべて古くなる。`unguarded` として数える |

## 5. probe の契約

```json
{ "type": "probe", "probeId": "probe-0007", "target": { "role": "button", "name": "保存" },
  "url": "http://127.0.0.1:3000/editor.html", "matches": 1, "visible": true, "enabled": true,
  "bbox": { "x": 902, "y": 712, "width": 88, "height": 40 }, "ready": true,
  "screenshot": "evidence/probe-0007.png", "fingerprint": "sha256:…" }
```

- 名前は results.json に合わせて camelCase。`screenshot` は記録の置き場からの相対パス（run の画像と同じ流儀）。
- `matches != 1` のときは `visibleMatches` と `reason` を返し、`bbox`・指紋・画像は持たない。
- `ready = matches == 1 && visible && enabled`。ready でなければ終了コード 1。
- 指紋は、要素の tag・type・role・id・name・aria-label・aria-labelledby・aria-expanded・表示文字（先頭 200 字、入力欄は読まない）・
  disabled・ページ座標の bbox を JSON にした sha256。中身は記録に残さない。
- ready のときだけ、ページ側に `window.__webTestProbes[probeId] = new WeakRef(要素)`（列挙されないプロパティ）を置く。

## 6. 古い probe の検知（3 段）

1. **世代**（ページに触らずに判定）: 見張り役は世代番号を持ち、action・navigation・other を通すたびに +1。
   probe はそのときの世代を覚え、違えば `stale`。操作 1 回ごとに取り直す、が規則。
2. **同一性**（ページ側）: 操作の直前に対象を同じ指定で引き直し、WeakRef の指す要素と同じかを見る。
   アプリが非同期に作り直した・別の文書に移った → `stale`。
3. **指紋**: 引き直した要素の指紋が probe 時と違えば `changed`（位置がずれた・属性が変わった）。
   エージェントが `--fingerprint` を付ければ、それとも突き合わせる（`fingerprintMismatch`）。

確かめ（2・3）と操作は別の `run-code` だが、操作する側でも同一性をもう一度見てから操作する。

## 7. 記録（`explore-evidence.jsonl`）

置き場は `web-test-results/explore-<日時>/`（`--evidence-dir`）。1 行 1 レコード、種類は 3 つだけ:

- `probe`: 上の契約そのまま
- `action`: `action` / `probeId` / `target` / `fingerprint` / `status`（done・rejected・failed）/ `reason` / 操作のあとの `url`。
  `fill` の値は常に `[伏せた]`（パスワードかどうかを見分けきれないため全部伏せる）。`select` の値・`press` のキーは残す
- `observation`: エージェントが `observe` で残した文（500 字まで）と URL、対象を付ければ一致数と見えているか

残さないもの: DOM・aria snapshot・依頼文・通信・入力値・URL のクエリとハッシュ。probe の状態（世代・指紋の一覧）は
依頼の作業ディレクトリに置き、generate の終わりに消える。

## 8. 比較（`scripts/compare-explore.js`）

同じ条件・同じ URL で baseline（`--explore`）と candidate（`--explore --probe-before-act`）を交互に作り、
作ったケースをすぐ `runSuites` で 1 回動かす。

| 記録する数字 | 出どころ |
|---|---|
| 書式の検査に合格 / 頼み直しの回数 | generate の成否と `attempts` |
| 初回の実行で合格 | `runSuites` の summary |
| 実行で 1 つに決まらない / 見つからない対象 | 失敗ステップの文言（strict mode violation / Timeout） |
| 確認で 1 つに決まらない / 無い・隠れ・無効 | probe の集計（candidate だけ。baseline は「対象外」） |
| 古い確認で断った / 確認なしで断った | 見張り役の集計 |
| ケースあたりの確認回数 / 作成時間 | 集計・壁時計 |
| 使用量（トークン） | 今の経路では取れないので `null`（「不明」）。0 にはしない |

### 8.1 この環境での実測（決まった手順のエージェント）

この環境には kiro-cli も copilot も無いため、実エージェントでの比較はまだ取れていない。代わりに、テスト用の
決まった手順のエージェント（`test/fixtures/fake-agent.js` の `naive`。最初は画面の文字「保存」で対象を指し、
見張り役があれば probe の結果を見て指し直す）で `editor.html` を各 2 回作った結果:

| 項目 | baseline | candidate |
|---|---|---|
| 書式の検査に合格 | 2/2 | 2/2 |
| 作ったケースが 1 回目で合格 | 0/2 | 2/2 |
| 実行で 1 つに決まらなかった対象 | 2 | 0 |
| 確認で 1 つに決まらなかった対象 | 対象外 | 2 |
| ケースあたりの確認回数 | 対象外 | 3 |
| 作成時間の平均 | 約 2.6 秒 | 約 10.9 秒（probe 1 回あたり約 1 秒の playwright-cli 呼び出し） |

これは仕組みが意図どおりに働くこと（取り違えを実行前に確認の段で捕まえる）を示すだけで、実エージェントの
成功率の改善を示すものではない。

## 9. 既定にする条件（今回は既定にしない）

次をすべて満たしたら `--explore` の既定を probe ありに切り替えることを検討する。

1. kiro-cli と copilot のそれぞれで、`editor.html` と実案件の画面 2 つ以上について各 5 回以上比較し、
   candidate の「1 回目で合格」が baseline 以上で、少なくとも 1 つの画面で明確に上回る
2. candidate の「書式の検査に合格」と「頼み直しの回数」が baseline より悪くない
3. 作成時間の増え方が許せる範囲（目安: 中央値で 2 倍以内）。超えるなら probe の呼び出しをまとめる改善を先にする
4. 断った操作のうち、エージェントが取り直して進めた割合が高い（断られて止まる・playwright-cli を直に叩いて
   回り道する、が目立たない）。回り道は `unguarded` と、記録に無い操作が YAML に現れることで見る
5. 記録に入力値・トークンが出ていないことを、実案件の記録で確かめる

切り替えたあとも `--no-probe-before-act` で今の動きに戻せるようにする。

## 10. 残した制約

- エージェントはシェルを持つので、playwright-cli を直に呼べば見張り役を通らない。依頼文では見張り役のコマンドしか教えず、
  回り道は記録の欠け（YAML にあるのに記録に無い操作）で気づく。
- `eval` / `run-code` などで状態を変えても止めない（`unguarded` として数え、probe を古くするだけ）。
- probe の画像は要素だけを撮る。入力欄に打った文字が表示される要素を撮ると、その文字は画像に写る
  （パスワード欄は伏せ字のまま写る）。
- 視覚だけで見える対象（canvas の中など）の登録は扱わない。
