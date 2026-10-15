# testcase-audit

自然文で書いたテストケース（e2e・シナリオテスト・人が見るチェックリスト）を、**テストケースとしての妥当性**で
点検する。LLM は使わない。同じ入力からは毎回同じ指摘が出る。

書き方の点検（重複・矛盾・漏れ・不備）に加えて、次を見る。

- **判定の強さ** … 期待結果を見て合否を決められるか。何が出ても通ってしまわないか
- **実行できるか** … 使うデータ・ログイン状態・始める画面が用意されているか
- **条件の組み合わせ** … ケースから「条件 → 結果」の表を取り出し、試していない組・食い違い・重なりを出す
- **仕様との突き合わせ**（`--spec`）… 要件・項目の制約（必須・文字数・形式）・状態遷移・権限・API の応答・文言
- **実装との突き合わせ**（`--source`）… 期待結果に書いた文言が、実装のどこかにあるか

仕様・実装は**ローカルのフォルダ**を渡す。別のリポジトリでもよい。

```text
$ testcase-audit examples/cases/login.yaml examples/cases/orders.yaml --spec examples/spec --source examples/app
examples/cases/login.yaml
   24  警告  I01 矛盾 LG-02  期待する文言「メールアドレスかパスワードが違いました」が実装のどこにもありません（実装には「メールアドレスかパスワードが違います」）
examples/cases/orders.yaml
   30  警告  O04 判定 ORD-02  エラーになることしか確かめておらず、別の理由で失敗しても通ります
   32  警告  S08 矛盾 ORD-03  「受付」で「取り消す」すると「発送済み」になると期待していますが、仕様の状態遷移にありません（仕様では「取消済み」）
   39  警告  X01 実行 ORD-03  「O-300」を取り消していますが、その「O-300」を前提でも前の手順でも用意していません
    5  参考  M01 漏れ ORD-01  条件の組み合わせ（2 つずつ）のうち 3 組を試していません: 役割=管理者×状態=発送済み、…
         → あと 2 件で全部の組を試せる。例: {役割: 管理者, 状態: 発送済み, 支払: カード} …
examples/spec/login.md
   14  警告  S02 漏れ  項目「パスワード」（必須・8〜20文字・英数字）の 未入力 / 境界 20文字・21文字 / 形式の誤り を試すケースがありません
examples/spec/orders.md
   24  警告  S04 漏れ  権限「注文 / 権限」の 4 組を試していません: ゲスト×注文を取り消す（できない）、…
   29  警告  S05 漏れ  API「POST /orders/{id}/cancel」の応答 404・409 を試すケースがありません
```

## 入れる

Node.js 18 以上。依存（YAML の読み取り・JSON Schema の検査・日本語の形態素解析）は `npm install` で入る。

```bash
cd tools/testcase-audit
npm install
npm install -g .                 # testcase-audit コマンドを入れる（入れずに node bin/testcase-audit.js でもよい）
```

## 使う

```bash
testcase-audit tests/cases/                                   # フォルダの .json / .yaml / .yml をすべて
testcase-audit cases.yaml --spec ../my-app-docs/docs           # 仕様と突き合わせる（繰り返し可）
testcase-audit cases.yaml --source ../my-app/src               # 文言が実装にあるかも見る（繰り返し可）
testcase-audit cases.yaml -f markdown -o report.md             # 表にして書き出す（ほかに -f json）
testcase-audit cases.yaml --fail-on warning                    # 警告以上があれば終了コード 1（既定は error）
testcase-audit spec ../my-app-docs/docs                        # 仕様から何を読み取ったかを確かめる
testcase-audit rules                                           # 規則の一覧
testcase-audit schema                                          # おすすめのケースの書式
```

終了コード: `0` 落とす重さの指摘なし / `1` あり / `2` 使い方の誤り。CI ではそのままゲートに使える。

### 設定ファイル

今のフォルダの `testcase-audit.config.yaml`（`-c` で別のファイル）を読む。パスは設定ファイルからの相対で、
別のリポジトリのフォルダも書ける。書いたものは引数と合わせて使う（ケースは引数が優先）。

```yaml
cases: [tests/cases]
spec: [../my-app-docs/docs]
source: [../my-app/src]
format:
  schema: schema/cases.schema.json   # ケースが従う書式（JSON Schema）。合わない所を D16 で出す
  fields: { expected: [確認ポイント] } # 項目名の綴りを足す
rules: { D11: off, G04: warning }    # 規則ごとに off / error / warning / info
similarity: { duplicate: 0.9, same_steps: 0.92 }
words:
  vague: [見栄えがよい]               # 曖昧な表現を足す（正規表現）
  negative: [差し戻し]                # 異常系とみなす語を足す（正規表現）
  pairs: [{ a_label: 会員, b_label: 非会員, a: ["(?<!非)会員"], b: [非会員] }]
requirement_id: "(?:REQ|FR|NFR)-\\d+" # 仕様の中の要件 ID の形
```

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

`testcase-audit schema` で出る書式（[schema/testcase.schema.json](schema/testcase.schema.json)）を LLM に渡す。
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

フォルダの下の `.md` `.yaml` `.yml` `.json` `.csv` を読む。`testcase-audit spec <フォルダ>` で、読み取った結果を確かめられる。

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

`testcase-audit rules` で一覧が出る。分類は 不備・重複・矛盾・漏れ・判定・実行 の 6 つ。

| コード | 分類 | 既定 | 内容 |
|---|---|---|---|
| D01〜D05 | 不備 | 警告・エラー | ID・タイトル・手順・期待結果がない、ID がファイルの中で重複 |
| D06 | 不備 | 警告 | 合否を決められない曖昧な表現（「正しく」「適切に」「など」「仕様どおり」…） |
| D07 | 不備 | エラー | 書きかけ（TBD・TODO・要確認・???…） |
| D08 / D09 | 不備 | 警告 | テストデータが決まっていない（「任意の」）/ ほかのケースに依存している（「同上」） |
| D10 / D11 / D12 | 不備 | 警告・参考 | 期待結果に操作 / 手順に確認 / 1 手順に操作が多い |
| D13 / D14 | 不備 | 警告 | 要件の紐づけがない / 仕様にない要件 ID |
| D15 / D16 | 不備 | エラー | 同じキーが 2 回（先の値が捨てられる）/ 指定した書式に合わない |
| U01〜U04 | 重複 | エラー〜参考 | 完全に同じ / ほぼ同じ / 値だけ違う（表にまとめられる）/ 同じタイトルで中身が違う |
| U05 | 重複 | 警告 | 同じ条件の組で同じ結果を確かめている |
| C01 / C02 / C03 | 矛盾 | エラー・警告 | 同じ手順で逆の期待結果 / ケースの中で食い違う / 同じ手順で期待結果が違う |
| C04 / C05 | 矛盾 | 警告・エラー | 数値の条件が食い違う / 同じ条件の組で結果が逆 |
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

同じファイルで同じ参考の指摘が 10 件を超えたら、1 件にまとめて出す。

## ほかの道具と組む

testcase-audit は単体で動く。ほかの道具とは、コマンドの終了コードと結果ファイルだけでつながる。

### 結果ファイル（`--evidence`）

`--evidence <file>` で、`{"version": 1, "items": [...]}` の形の結果ファイルも書く。

| id | 中身 |
|---|---|
| `testcase-audit/errors` | エラーの件数（`max: 0`） |
| `testcase-audit/warnings` | 警告の件数 |
| `testcase-audit/defect` `duplicate` `conflict` `gap` `oracle` `exec` | 分類ごとの件数 |
| `testcase-audit/<ケースのファイル>` | ファイルごとの合否（`--fail-on` の重さ以上があれば failed。最初の指摘を `error` に） |

### codd-agent と組む

[codd-agent](../codd-agent/README.md) の「変えたあとの検査」（`check`）に入れると、ケースや仕様を変えるたびに
点検し、落ちたファイルと理由を codd-agent が示す。仕様のリポジトリは codd-agent の参照先と同じフォルダを渡す。

```bash
python3 tools/codd-agent/init.py ~/work/my-app --side impl --ref docs=../my-app-docs \
  --check "testcase-audit tests/cases --spec ../my-app-docs/docs --source src --evidence results/testcase-audit.json" \
  --evidence "results/testcase-audit.json"
```

文書に件数を写すなら、codd-agent の印（`<!-- evidence: testcase-audit/errors -->`）を使う。

## 分かること・分からないこと

文字の並び・否定の向き・数値・表の対応で判断する。次は**分からない**ので、人か LLM のレビューと組み合わせる。

- 言い換えの矛盾（「ログイン画面に戻る」と「ホーム画面へ移る」は、別の文としか分からない）
- 期待結果が仕様として正しいか（仕様と実装に書かれた文言・遷移・制約と照らせる分だけ）
- 仕様に書かれていない観点の漏れ

仕組みの詳細は[設計書](../../docs/designs/testcase-audit-design.md)にある。

## 開発

```bash
cd tools/testcase-audit && npm install && npm test
```
