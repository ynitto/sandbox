# test-evidence

任意のテストコマンドを実行し、JUnit XML をケースと個別テストを対応付けた JSON の実行結果にする。
Python 3 の標準ライブラリだけで動く。ケースの内部の書式・言語・結果の読み手には依存しない。

## 実行

プロジェクトのルートで実行する。JUnit をファイルに書くランナーの例:

```bash
python3 /path/to/test-evidence/run.py \
  --bindings test-bindings.json --suite unit \
  --junit results/unit.xml --output results/unit.json -- \
  python3 -m pytest --junitxml=results/unit.xml
```

Node.js の標準ランナーのように XML を標準出力へ書く場合は `--junit -` を使う。

```bash
python3 /path/to/test-evidence/run.py \
  --bindings test-bindings.json --suite e2e \
  --junit - --output results/e2e.json -- \
  node --test --test-reporter=junit tests/e2e.test.cjs
```

`--timeout` は秒数（既定 600）。`--` 以降は引数を分けたコマンドで、シェルとして解釈しない。
Windows では利用している Python のコマンド名を使う。

## 対応表

```json
{
  "version": 1,
  "bindings": [{
    "case": {"file": "tests/cases.yaml", "id": "SHIP-01"},
    "targets": [{"suite": "unit", "file": "tests/test_shipping.py", "selector": "ShippingTest::test_free"}]
  }]
}
```

パスはプロジェクトルートからの相対。selector は JUnit の `classname::name` または `name` と一致させる。
同名テストが複数のファイルにあり、JUnit に `file` が無ければ曖昧な対応として失敗する。
1 ケースに複数のテストを対応させても、同じテストに複数のケースを対応させてもよい。
別リポジトリのケースには `case.side` と `--repo docs=../design` を使う。
このリポジトリ自身に名前を付ける場合は `--side app` を指定する。

## 結果と鮮度

- [対応表のスキーマ](schema/bindings.schema.json) / [実行結果のスキーマ](schema/execution.schema.json)
- 結果は `version: 1` / `purpose: execution` / `root` / `items` の JSON。
- 個別テストには suite・selector・テストのハッシュと、対応するケースの ID・元ファイル・ハッシュを持つ。
- 実行前にケースとテストのハッシュを控え、終了後に変わっていれば結果を採用しない。
- `TEST_EVIDENCE_RUN_ID` と `TEST_EVIDENCE_INPUTS_DIGEST` が渡されれば、その実行の識別情報を結果に引き継ぐ。未指定でも単独で動く。
- ファイルに書く JUnit と以前の出力 JSON は実行前に削除する。ランナーが何も書かなければ古い成功を再利用しない。
- ランナーの失敗、対応先の欠落、skip、未知の JUnit status は終了コード 0 にしない。
- `results/` などの生成物は `.gitignore` や実行側の除外設定に入れる。

JUnit を書けるランナーが対象である。独自形式のランナーはプロジェクト側で JUnit に変換するか、
公開した JSON の契約を直接実装できる。結果変換の成功だけでは、期待結果の正しさは保証しない。

## 実行例

[送料計算の例](examples/shipping/verify.py) は、YAML のケースを点検して一覧を生成し、Python の単体テスト 2 件と
Node.js から Python の CLI を呼ぶ E2E 1 件を実行する。ブラウザは使わない。

```bash
python3 tools/test-evidence/examples/shipping/verify.py
```

この例には Node.js と、隣の testcase-lint の依存のインストールが必要。実行結果は例の `results/` に出る。
`run_unit.py` はプロジェクト側で unittest の結果を JUnit に変換する最小例である。

```bash
python3 -m unittest discover -s tools/test-evidence/tests
```
