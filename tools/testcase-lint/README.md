# testcase-lint

自然文で書いたテストケース（e2e・シナリオテスト・人が見るチェックリスト）を、**テストケースとしての妥当性**で
点検する。LLM は使わない。同じ入力からは毎回同じ指摘が出る。

テストコードの重複・統合候補は [`testcase-lint code`](#テストコードの静的検査) で検査する。
自然文ケースを点検する既存の使い方はそのまま使える。

- **曖昧さ** … 読む人によって、使う値・操作・合否が変わってしまう書き方（「正しく」「任意の」「しばらく」「該当の」
  「AまたはB」「〜ことがある」「前より速く」「各項目を入力」など）
- **書き方** … 重複・矛盾・漏れ・不備
- **判定の強さ** … 期待結果を見て合否を決められるか。何が出ても通ってしまわないか
- **実行できるか** … 使うデータ・ログイン状態・始める画面が用意されているか
- **条件の組み合わせ** … ケースから「条件 → 結果」の表を取り出し、試していない組・食い違い・重なりを出す
- **仕様との突き合わせ** … 要件・項目の制約（必須・文字数・形式）・状態遷移・権限・API の応答・文言
- **実装との突き合わせ** … 期待結果に書いた文言が、実装のどこかにあるか

## 入れる

Node.js 18 以上と npm があればよい（Windows・macOS・Linux 共通）。グローバルに入れる:

```bash
node tools/testcase-lint/install.js            # testcase-lint コマンドを入れる（入っていれば入れ直す）
node tools/testcase-lint/install.js --check    # 入れたあと、同梱の例で動くか確かめる
```

`npm pack` で固めたものを `npm install -g` で入れる（依存の YAML・JSON Schema・形態素解析の辞書も一緒に入る）。
フォルダを直接 `npm install -g .` すると、このフォルダへのリンクが置かれるだけで、リポジトリを動かす・消すと
コマンドが壊れるので使わない。権限が足りないと言われたら、npm の置き場をユーザーのフォルダにしてから入れ直す
（`npm config set prefix ~/.npm-global` のあと、`~/.npm-global/bin` を PATH に足す）。
外すときは `npm uninstall -g testcase-lint`。入れずに使うなら、このフォルダで `npm install` してから
`node bin/testcase-lint.js` を呼ぶ。

## 使う

**ふだんはオプション無しで `testcase-lint` だけを実行する。** 何を点検するかは設定ファイルに書いておく。

```bash
cd ~/work/my-app
testcase-lint init           # 最初に 1 回。tests/cases・docs/specs・src などを探して設定ファイルを作る
testcase-lint                # これだけで、ケースを仕様・実装と突き合わせて点検する
```

```text
$ testcase-lint
ケース: cases / 仕様: spec / 実装: app（testcase-lint.config.yaml）

cases/checklist.json
    4  警告  A01 曖昧  「適切な」では、人によって合否が変わります
         「エラー時に適切なメッセージが表示されること」
         → 何が・どうなれば合格かを、見て判断できる形（表示される文言・値・件数・状態）で書く
cases/login-cases.yaml
    5  エラー C01 矛盾 TC-01, TC-04  前提と手順が同じなのに、期待結果が逆です（「ホーム画面が表示される」と「ホーム画面が表示されない」）
   39  警告  S06 矛盾 TC-03  期待する文言「パスワードは8文字以上で入力してください」が仕様にありません（仕様では「パスワードは8文字以上20文字以下で入力してください」）
   58  警告  A02 曖昧 TC-05  「任意の」では、人によって使う値が変わります
cases/orders.yaml
   30  警告  O04 判定 ORD-02  エラーになることしか確かめておらず、別の理由で失敗しても通ります
   32  警告  S08 矛盾 ORD-03  「受付」で「取り消す」すると「発送済み」になると期待していますが、仕様の状態遷移にありません（仕様では「取消済み」）
   39  警告  X01 実行 ORD-03  「O-300」を取り消していますが、その「O-300」を前提でも前の手順でも用意していません
spec/orders.md
   24  警告  S04 漏れ  権限「注文 / 権限」の 4 組を試していません: ゲスト×注文を取り消す（できない）、…
   29  警告  S05 漏れ  API「POST /orders/{id}/cancel」の応答 404・409 を試すケースがありません
```

（[examples/](examples/) で `testcase-lint` を実行したときの出力から抜粋）

1 行目に、何を点検したか（ケース・仕様・実装のフォルダと、読んだ設定ファイル）が出る。
終了コード: `0` 落とす重さの指摘なし / `1` あり / `2` 使い方の誤り・点検するものが無い。CI ではそのままゲートに使える。

### 設定ファイル

`testcase-lint` は、今のフォルダから親へさかのぼって最初に見つかった `testcase-lint.config.yaml` を読む
（リポジトリのルートに置けば、どの下のフォルダで実行しても同じ点検になる）。パスは設定ファイルからの相対で、
**別のリポジトリのフォルダも書ける**。

```yaml
cases: [tests/cases]                 # 点検するケース
spec: [../my-app-docs/docs]          # 仕様のフォルダ（別のリポジトリでもよい）
source: [src]                        # 実装のフォルダ
evidence: results/testcase-lint.json # 結果ファイルも書く（ほかの道具に渡す）
fail_on: error                       # この重さ以上があれば終了コード 1（error / warning / info / never）
min_severity: info                   # この重さ未満は出さない
format:
  schema: schema/cases.schema.json   # ケースが従う書式（JSON Schema）。合わない所を D16 で出す
  fields: { expected: [確認ポイント] } # 項目名の綴りを足す
rules: { D11: off, G04: warning }    # 規則ごとに off / error / warning / info
similarity: { duplicate: 0.9, same_steps: 0.92 }
words:
  vague: [見栄えがよい]               # 合否の基準があいまいな語を足す（正規表現）
  negative: [差し戻し]                # 異常系とみなす語を足す（正規表現）
  pairs: [{ a_label: 会員, b_label: 非会員, a: ["(?<!非)会員"], b: [非会員] }]
requirement_id: "(?:REQ|FR|NFR)-\\d+" # 仕様の中の要件 ID の形
test_data:
  domains: [staging.my-corp.co.jp]   # テスト用のメールアドレスのドメインを足す（D18。末尾一致）
  cards: ["4000 0000 0000 0077"]     # テスト用のカード番号を足す（D19）
```

設定ファイルが無いときは、今のフォルダの下のよくあるフォルダを探して使う（あるものをすべて）。

| 対象 | 探すフォルダ |
|---|---|
| ケース | `tests/cases` `test/cases` `tests/testcases` `testcases` `test-cases` `tests/scenarios` `scenarios` `tests/e2e` `test/e2e` `e2e` `cases` |
| 仕様 | `docs/specs` `docs/spec` `docs/requirements` `specs` `spec` `requirements` |
| 実装 | `src` `app` |

`testcase-lint init` は、この探し方で見つけたフォルダを書いた設定ファイルを作る（`--cases` `--spec` `--source` で
指定もできる。既存の設定は上書きしない）。

### その場で対象を渡す（init も設定ファイルも要らない）

ケースのファイル・フォルダ・glob を引数に渡せば、`init` をせず設定ファイルが無くても動く。

```bash
testcase-lint qa/login.yaml                           # ファイル
testcase-lint qa/                                     # フォルダ（下の .json / .yaml / .yml をすべて）
testcase-lint "qa/**/*.yaml" "e2e/*.json"             # glob（いくつでも）
testcase-lint "qa/**/*.yaml" --spec "../my-app-docs/docs/**/*.md" --source ../my-app/src
testcase-lint qa/ -f markdown -o report.md            # 表にして書き出す（ほかに -f json）
testcase-lint qa/ --fail-on warning                   # 警告以上があれば終了コード 1
testcase-lint spec ../my-app-docs/docs                # 仕様から何を読み取ったかを確かめる
testcase-lint rules                                   # 規則の一覧
testcase-lint schema                                  # おすすめのケースの書式
```

- glob は `**`（0 個以上のフォルダ）・`*`・`?`・`{a,b}`・`[a-z]` が使える。testcase-lint 自身が展開するので、
  Node の版にも Windows のシェルにもよらず同じに動く。シェルに先に展開させないよう `"` で囲む
  （bash は `**` をフォルダをまたがない `*` として扱ってしまう）
- `--spec` `--source` にもフォルダ・ファイル・glob を渡せる（繰り返し可）
- 設定ファイルがあるフォルダで引数を渡したときは、設定ファイルの `spec` `source` も合わせて使う。
  設定ファイルが無いときに見つけた仕様・実装のフォルダは使わない（その場で渡したケースと関係の無い `src` を読まないため）

## ケースの書式

### 読める形

JSON / YAML なら、項目名は英語・日本語・Gherkin のよくある綴りを受ける。LLM の出力によくある ```` ``` ```` の
囲みも、行番号を保ったまま読む。ケースの列は、ファイルの一番上の配列か、`cases` `tests` `scenarios`
`checklist` `テストケース` などのキーの下に置く。

| 意味 | 受ける項目名の例 |
|---|---|
| ID | `id` `case_id` `no` `テストID` `項番` |
| タイトル | `title` `name` `scenario` `テスト項目` `確認項目` |
| 前提 | `preconditions` `given` `setup` `前提条件` `事前条件` |
| 手順 | `steps` `when` `actions` `手順` `操作手順` |
| 期待結果 | `expected` `then` `assertions` `期待結果` `確認内容` |
| 要件 | `requirement` `requirements` `refs` `要件` `要件ID` |
| 条件の組 | `conditions` `params` `data` `条件` `テストデータ` |

- 手順の要素が `{action: …, expected: …}` なら、期待結果を分けて読む。`expect:` `verify:` も期待結果として読む
- 文字列だけの配列（`checklist: [...]`）は、人が見るチェックリストとして読む（手順・期待結果を求めない）
- 上の表にない項目（モック・言語設定など）は前提として比べる。同じ手順でも前提が違えば別のケースとみなす

### 決まった書式に合わせる

チームの書式があるなら、JSON Schema にして設定の `format.schema` に書く。合わない所（足りない項目・
書式にない項目・型の違い）を行番号付きで出す。項目名が独自なら `format.fields` で別名を足す。

### おすすめの書式（LLM に書かせるとき）

`testcase-lint schema` で出る書式（[schema/testcase.schema.json](schema/testcase.schema.json)）を LLM に渡す。
条件と期待結果を分けて書くと、組み合わせ・判定の強さの点検が推測なしで効く。

```yaml
feature: 注文の取り消し
cases:
  - id: ORD-01
    title: 受付の注文を一般会員が取り消せる
    requirement: REQ-10                       # 仕様の要件 ID と同じ綴り
    conditions: { 役割: 一般会員, 状態: 受付, 支払: カード }   # 条件の組
    preconditions:                            # ログインしている利用者・用意するデータ・開いている画面
      - 一般会員 member@example.com でログイン済み
      - 状態が受付の注文 O-100 がある
    steps:                                    # 1 手順 1 操作。入力する値は「」で囲む
      - 注文履歴画面を開く
      - 注文 O-100 の取り消すボタンを押す
    expected:                                 # 見て確かめられる結果。文言は「」で囲む
      - 「注文を取り消しました」と表示される
      - 注文 O-100 の状態が取消済みになる
```

LLM への指示に添えるとよいこと:

- 期待結果には、表示される文言・値・件数を書く（「正しく表示される」「エラーになる」だけにしない）
- 失敗するケースでは、データが変わっていないこと（保存されていない・件数が変わらない）も書く
- 使うデータ・ログインする利用者・始める画面を前提に書く（「前のケースと同じ」と書かない）
- 条件の組は `conditions` に書き、値の違うケースを並べる

## 仕様のフォルダから読み取るもの

フォルダの下の `.md` `.yaml` `.yml` `.json` `.csv` を読む。`testcase-lint spec <フォルダ>` で、読み取った結果を確かめられる。

| 読み取るもの | 書き方の例 |
|---|---|
| 要件 | 見出し・箇条書き・表の 1 列目に書いた `REQ-01` `FR-1` などの ID と本文。`{id, text}` の YAML の一覧 |
| 項目の制約 | 見出しに「項目名」と「必須・桁数・文字数・最大・最小・範囲・形式・選択肢」のどれかがある表。JSON Schema・OpenAPI の `minLength` `maxLength` `minimum` `maximum` `required` `enum` |
| 状態遷移 | 「現在の状態・イベント・次の状態」の表。Mermaid の `stateDiagram`（`受付 --> 発送済み : 発送する`）。`transitions: [{from, event, to}]` の YAML |
| 権限 | 1 列目が操作、ほかの列が役割で、○ × などの印が並ぶ表 |
| API | OpenAPI の `paths` と応答コード。本文の `POST /orders/{id}/cancel` と、同じ節に書いた応答コード |
| 文言 | 「メッセージ・表示内容・文言」の列の値。本文で「」に囲んだ 4 文字以上の文 |

仕様は広く、ケースは一部の機能だけを扱うことが多い。そこで、表・図・API ごとに**ケースが 1 つでも触れて
いるもの**だけを突き合わせる。どのケースも触れていない仕様のファイルは、結果の最後に「対象外」として並ぶ。

## 指摘すること

`testcase-lint rules` で一覧が出る。分類は 曖昧・不備・重複・矛盾・漏れ・判定・実行 の 7 つ。

| コード | 分類 | 既定 | 内容 |
|---|---|---|---|
| A01 | 曖昧 | 警告 | 合否の基準があいまい（「正しく」「適切に」「問題なく」「仕様どおり」「崩れていない」「など」…）。期待結果とチェック項目に当てる |
| A02 | 曖昧 | 警告 | 使う値が決まっていない（「任意の」「適当な」「正しいパスワード」「有効な値」…で、「」の値が無い） |
| A03 | 曖昧 | 警告 | 量・回数・時間があいまい（「複数」「いくつか」「しばらく」「数秒」「長い文字列」「大きなファイル」…で、数値が無い） |
| A04 | 曖昧 | 警告 | 指すものがあいまい（「該当の」「当該」「所定の」「最新の」「必要な項目」…、最初の文の「その」「これ」） |
| A05 | 曖昧 | 警告 | 期待結果に選択肢がある（「または」「いずれか」「どちらか」…。どちらになっても合格になる） |
| A06 | 曖昧 | 警告 | 期待結果を言い切っていない（「ことがある」「場合がある」「はず」「基本的に」「ほぼ」、二重否定…） |
| A07 | 曖昧 | 警告 | 比べる基準がない（「前より速く」「改善される」…で、数値が無い） |
| A08 | 曖昧 | 警告 | 操作の対象があいまい（「ボタンを押す」「各項目を入力する」「必要事項を入力する」…） |
| A09 | 曖昧 | 参考 | 前提・手順の列挙が閉じていない（「名前・住所などを入力」「その他」…） |
| D01〜D05 | 不備 | 警告・エラー | ID・タイトル・手順・期待結果がない、ID がファイルの中で重複 |
| D07 | 不備 | エラー | 書きかけ（TBD・TODO・要確認・???…） |
| D09 | 不備 | 警告 | ほかのケースに依存している（「同上」「前のケースと同じ手順で」） |
| D10 / D11 / D12 | 不備 | 警告・参考 | 期待結果に操作 / 手順に確認 / 1 手順に操作が多い |
| D13 / D14 | 不備 | 警告 | 要件の紐づけがない / 仕様にない要件 ID |
| D15 / D16 | 不備 | エラー | 同じキーが 2 回（先の値が捨てられる）/ 指定した書式に合わない |
| D17 | 不備 | 警告 | ケースを 1 件も読み取れない（空のファイル・項目名の綴り違い。何も点検されないまま「指摘なし」になるのを防ぐ） |
| D18 | 不備 | 警告 | 実在しうるメールアドレス（example.com・*.test などの予約済みドメインと `test_data.domains` 以外） |
| D19 | 不備 | エラー | 本物らしいカード番号（ブランドの形・Luhn が合い、公開のテスト番号でない）、形の決まった鍵（AWS・GitHub・Slack・JWT・秘密鍵…）。値そのものは出力に写さない |
| U01〜U04 | 重複 | エラー〜参考 | 完全に同じ / ほぼ同じ / 値だけ違う（表にまとめられる）/ 同じタイトルで中身が違う |
| U05 | 重複 | 警告 | 同じ条件の組で同じ結果を確かめている |
| C01 / C02 / C03 | 矛盾 | エラー・警告 | 同じ手順で逆の期待結果 / ケースの中で食い違う / 同じ手順で期待結果が違う |
| C04 / C05 | 矛盾 | 警告・エラー | 数値の条件が食い違う / 同じ条件の組で結果が逆 |
| C06 / C07 | 矛盾 | 警告・参考 | 期待する文言が、ほかのケースと句読点・空白だけ違う / 1〜2 文字だけ違う（仕様・実装に片方があれば S06・I01 に任せる） |
| G02 / G03 / G04 | 漏れ | 警告・参考 | 異常系がない / 境界値（ちょうど・1 つ外）を試していない / 対になる条件の片方だけ |
| M01 / M02 | 漏れ | 参考・警告 | 条件の組み合わせ（2 つずつ）の漏れと、埋める行の提案 / 通る値しか試していない条件 |
| O01 | 判定 | 警告 | 期待結果が見て確かめられない（「DB に保存される」） |
| O02 | 判定 | 警告 | 期待結果が弱い（「画面が表示される」「エラーにならない」だけ） |
| O03 / O04 | 判定 | 参考・警告 | 失敗したとき変わらないことを見ていない / エラーになることしか見ていない |
| X01 / X02 / X03 | 実行 | 警告・参考 | 用意していないデータを使う / ログインせずにログイン後の操作 / 始める画面がない |
| S01〜S05 | 漏れ | エラー・警告 | 仕様の要件・項目の制約・状態遷移・権限・API の応答を試していない |
| S06 / S08 | 矛盾 | 警告 | 期待する文言が仕様と違う / 仕様にない状態遷移を期待している |
| S07 | 漏れ | 参考 | 仕様の文言をどのケースも確かめていない |
| I01 | 矛盾 | 警告 | 期待する文言が実装にない（近い文言があれば示す。`{0}` `%s` などの差し込みは任意の値として照らす） |
| I02 | 矛盾 | 警告 | 期待するエラーコード（E1001・ORDER_NOT_FOUND…）が実装にない。エラーを言う期待結果だけで拾う |

同じファイルで同じ参考の指摘が 10 件を超えたら、1 件にまとめて出す。

## ほかの道具と組む

testcase-lint は単体で動く。ほかの道具とは、コマンドの終了コードと結果ファイルだけでつながる。

### 結果ファイル（`evidence`）

設定の `evidence:`（か `--evidence <file>`）で、`{"version": 1, "items": [...]}` の形の結果ファイルも書く。
ルートには `purpose: check` を付ける。これはケースを点検した結果であり、実際のテストを実行した証拠とは区別する。
既存の項目 ID・合否・件数は変わらない。

`-f json` の出力には、指摘と要約に加えて正規化した `cases` が入る。
各ケースの `file` / `id` / `input_sha256` を、個別テストとの対応やケース一覧の検査に利用できる。
全単体テストを自然文ケースへ変換する必要はなく、追跡したい業務条件・公開契約から使える。

### 任意の入力変換と元ファイルへの指摘

ケースの `origin` に、JSON/YAML からの相対の入力元ファイル、正の整数の `line`、`input_sha256`、
`fields` の `preconditions` / `steps` / `expected` の行番号配列を付けると、指摘を元ファイルへ返す。
元ファイルが変わっていれば D00 で再生成を求める。`origin` と任意の `group` は比較する前提に加えない。
任意の JSON Schema の指摘と evidence の失敗判定にも入力元を反映する。

[gherkin-cases](../gherkin-cases/README.md) はこの形式へ `.feature` を変換する独立したツール。
この本体に Gherkin のパーサーや Cucumber の実行エンジンを追加する必要はない。

```bash
gherkin-cases specs/shipping.feature -o generated/cases.json
testcase-lint generated/cases.json -f json -o results/cases.json
```

`.feature` を直接このコマンドへ渡す機能はない。生成した JSON を点検し、元ファイルを正本として更新する。

### 結果の項目

| id | 中身 |
|---|---|
| `testcase-lint/errors` | エラーの件数（`max: 0`） |
| `testcase-lint/warnings` | 警告の件数 |
| `testcase-lint/ambiguity` `defect` `duplicate` `conflict` `gap` `oracle` `exec` | 分類ごとの件数 |
| `testcase-lint/<ケースのファイル>` | ファイルごとの合否（`--fail-on` の重さ以上があれば failed。最初の指摘を `error` に） |

### codd-agent と組む

[codd-agent](../codd-agent/README.md) の「変えたあとの検査」（`check`）に、オプション無しの `testcase-lint` を入れる。
何を点検するか（ケース・仕様のフォルダ・結果ファイル）は `testcase-lint.config.yaml` に書いておく。
仕様のフォルダには、codd-agent の参照先と同じフォルダを書く。

```yaml
# ~/work/my-app/testcase-lint.config.yaml
cases: [tests/cases]
spec: [../my-app-docs/docs]
source: [src]
evidence: results/testcase-lint.json
```

```bash
python3 tools/codd-agent/init.py ~/work/my-app --side impl --ref docs=../my-app-docs \
  --check "testcase-lint" --evidence "results/testcase-lint.json"
```

ケースや仕様を変えるたびに点検し、落ちたファイルと理由を codd-agent が示す。文書に件数を写すなら、
codd-agent の印（`<!-- evidence: testcase-lint/errors -->`）を使う。

## テストコードの静的検査

JavaScript / TypeScript（JSX / TSX を含む）と Python のテストを AST で比較する。
Vitest・Jest・Node.js `node:test`・Playwright の import / require、pytest の関数、unittest のクラスを読む。
テスト・アプリ・プロジェクト設定・import 先のヘルパーは実行しない。Vitest 自体のインストールも不要。
Python のファイルを読む場合だけ Python 3.8 以上が必要。

```bash
testcase-lint code tests/ -f json -o results/code-report.json
testcase-lint code "src/**/*.test.ts" --fail-on warning
testcase-lint code tests/test_shipping.py
```

| 規則 | 既定 | 指摘とまとめ方 |
|---|---|---|
| K01 | warning | 同じファイルで本文と静的な実行条件が一致する。目的・観測する状態を確認して重複を整理する |
| K02 | info | 構造が同じで数値・文字列などが違う。test.each / parametrize / subTest で、各ケースを行として残す |
| K03 | warning | 同じファイルで suite / class を含むテストの完全名が重複する。ケースを区別できる名前にする |
| K04 | info | 別のファイルで本文と静的な実行条件が一致する。単体・結合・E2E の役割を確認する |
| K05 | warning | 同じパラメーター表の行が一致する。意図的な繰り返しかを確認する |
| K00 | warning | 動的な登録・表・構文などを静的に確定できない。解析済みの範囲と区別する |

ローカル変数名は束縛を解決してそろえ、リテラルを残した本文と、リテラルの値だけを伏せた構造を比較する。
外側の変数・import の参照先・hook・mock・fixture・引数・修飾子・登録間の初期化は比較条件に含める。
関数・クラス・プロパティの名前を、通常のローカル変数名として消さない。
値の違う境界値を「不要な重複」として削除する指摘にはしない。ソースを書き換える機能はない。

### 設定とCI

```yaml
code:
  files: [tests, "src/**/*.test.ts"]
  exclude: ["**/fixtures/**", "**/generated/**"]
  globals: false
  python: python3
  max_file_bytes: 2097152
  evidence: results/code-check.json
rules:
  K01: warning
  K02: info
  K04: off
```

設定のパスは設定ファイルからの相対。引数のパスがあれば `code.files` より優先する。
`code.files` が無い場合は現在のフォルダ以下から `.test.*` / `.spec.*` / `test_*.py` / `*_test.py` を探す。
隠しフォルダ・node_modules・dist・build・coverage・Python のキャッシュはたどらない。
Vitest の設定や `.gitignore` からの対象推定は行わない。独自の対象や生成物は files / exclude に指定する。

globals を使うプロジェクトでは `--globals` または `code.globals: true` を指定する。
import も設定も無い `test` / `it` を、勝手にテストと判定しない。
`--fail-on warning` を付けると warning 以上が終了コード 1 になる。info も止めるなら `--fail-on info`。
`--min-severity` は表示の絞り込みで、ゲートの判定を弱めない。

終了コードは 0 = 設定した重さの指摘なし、1 = 指摘あり、2 = 入力不備・テスト0件・未解析部分あり。
`--allow-incomplete` を付ければ部分的な結果で続けられるが、0件は成功にせず、出力にも不完全と記す。
K00 を off にしても、未解析の事実は消さない。

JSON には `tests`、`findings`、`analysis.complete`、`analysis.diagnostics`、理由付きの `analysis.suppressions` が入る。
自然文モードの `cases` 一覧とは区別する。text / markdown にも解析範囲を表示する。
evidence は `purpose: check`。自然文モードと出力が衝突しないように `code.evidence` を使う。
これは実際のテストの実行成功を表すものではない。

### 意図的に並べたケース

次の宣言または表の行に対し、規則と理由を明示して抑制できる。Python は `#` を使う。

```ts
// testcase-lint-code-ignore-next-line K01: 並列リクエストの独立した経路を残す
test('別経路での確認', () => { /* ... */ });
```

`K01,K02` のように複数指定もできる。理由の無いコメントは K00。
同じ重複グループに他の未抑制のテストが2件以上あれば、その重複は引き続き報告する。

### 解析できる範囲と他の言語

直接書いた登録とローカルのコールバック、インラインのリテラル表、他に変更・受け渡しの無いローカル定数の表を扱う。
動的タイトル、ループや条件分岐内の登録、動的な表・タグ付きテンプレートの表、複数の parametrize の直積、未知の継承・デコレーター、
eval / locals などの名前の観測は推測せず K00 にする。外部ヘルパーの本体は展開しない。
`complete: true` は対応する登録形式について不明部分が無いという意味で、ランナーの収集結果や実行時の等価性の証明ではない。
独自のテスト登録ラッパーや未対応のランナーの収集規約は、別アダプターで扱える。

他の言語は [中間表現のスキーマ](schema/test-code.schema.json) に合わせた AST アダプターを用意して渡す。
比較層は言語・パーサー・ランナーを知らず、本文・構造・実行条件のキーを読む。

```bash
testcase-lint code --ir results/custom-test-ir.json --fail-on warning
```

中間表現は `version: 1` と `tests` を持ち、各テストに file / line / title / fullTitle / framework / comparable /
staticTitle / context / body.exact / body.structural を入れる。body.exact は値を残した本文、body.structural は値だけを型で置き換えた構造のキー。
アダプター側で fixture・スコープ・外部参照を含む context を作り、不明な部分は diagnostics と comparable: false で返す。

[vitest-dedup](https://github.com/figueiredoluiz/vitest-dedup) の「本文・実行条件・値違いを分け、指摘の種類ごとに整理方法を示す」方針を参考にした。
近似的な振る舞い一致、包含関係、hook や suite 全体の統合はこの版の検出対象に含めない。

## 分かること・分からないこと

文字の並び・否定の向き・数値・表の対応で判断する。次は**分からない**ので、人か LLM のレビューと組み合わせる。

- 言い換えの矛盾（「ログイン画面に戻る」と「ホーム画面へ移る」は、別の文としか分からない）
- 期待結果が仕様として正しいか（仕様と実装に書かれた文言・遷移・制約と照らせる分だけ）
- 仕様に書かれていない観点の漏れ

仕組みの詳細は[設計書](../../docs/designs/testcase-lint-design.md)にある。

## 開発

```bash
cd tools/testcase-lint && npm install && npm test
```

入れた版を作り直すときは、もう一度 `node install.js` を実行する。
