# gherkin-cases

Gherkin の `.feature` を、入力元の位置とケース ID を保持した JSON のケースへ変換する。
公式パーサーを使う独立した入力ツールであり、Cucumber の実行エンジンやステップ定義は不要。
変換後は JSON/YAML を読む道具に渡せる。

```bash
cd tools/gherkin-cases
npm install
node bin/gherkin-cases.js /path/to/shipping.feature -o /path/to/generated/cases.json
```

Node.js 18 以上。`npm pack` で固めて `npm install -g <tgz>` すれば `gherkin-cases` コマンドとしても使える。
複数の `.feature` を並べて渡せる。`-o` が無ければ標準出力に JSON を出し、警告は標準エラーに出す。
標準出力の入力元パスは現在のフォルダが基準。別フォルダに保存する場合は `-o` を指定する。

## ID と展開

```gherkin
# language: ja
@requirement:REQ-12
機能: 送料
  シナリオアウトライン: 商品合計<金額>円の送料
    前提 配送先は東京都である
    もし 商品合計<金額>円の注文の送料を計算する
    ならば 送料は<送料>円になる
    例:
      | case_id | 金額 | 送料 |
      | SHIP-01 | 4999 | 500  |
      | SHIP-02 | 5000 | 0    |
```

- 通常の Scenario には `@case:SHIP-01`、Outline の各行には `case_id` 列を推奨する。
- 明示 ID が無ければ名前・グループ・例の内容から ID を生成して警告する。行の並べ替えでは変わらないが、名前・値の変更で変わる。
- Feature / Rule のグループ、タグの継承、各 Background、And / But、Examples、DataTable、DocString を展開する。
- `@requirement:ID` と `@REQ-数字` / `@FR-数字` / `@NFR-数字` を要件の参照として出す。
- `conditions` には Given / When で使う例の列だけを入れる。期待結果だけの値とケース ID は、条件の組み合わせに数えない。
- ステップの役割が決まらない先頭の `*`、重複 ID、空の case_id、構文エラーは終了コード 2。出力済みのファイルは上書きしない。

## 入力元の位置

`origin` には出力 JSON からの相対パス、元のケース・定義・例・各ステップの行、入力ファイルの SHA-256 を付ける。
JSON/YAML を読む側がこの一般的な位置情報に対応していれば、元のファイルの行へ指摘を戻せる。
入力元が変わったら再生成する。JSON を手で直して元の `.feature` と別の期待結果を持たせない。
AST 全体からの書き戻しや双方向同期は行わない。

隣の testcase-lint へ渡す例（そのプロジェクトのルートで実行）:

```bash
gherkin-cases specs/shipping.feature -o generated/cases.json
testcase-lint generated/cases.json -f json -o results/cases.json
```

ケース一覧に出る `file` は元の `.feature`。個別テストとの対応にも元ファイルとケース ID を使う。
`.feature` の読み取りを必要としないプロジェクトに、このツールを導入する必要はない。

```bash
npm test
```
