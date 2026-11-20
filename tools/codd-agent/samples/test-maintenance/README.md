# 割引計算で試すテスト自動保守

商品合計が 10,000 円以上なら 10% を割り引き、未満なら割引額は 0 円です。割引額の 1 円未満は切り捨てます。
境界の 10,000 円と 9,999 円を YAML のケースに書き、対応表で Python 標準ライブラリの unittest に結び付けています。

- **点検（check）** は、ケースの書き方や対応先を確認し、`results/case-check.json` に結果を残します。
- **実行（execution）** は、対応するテストを動かし、個別テストの結果を `results/unit.json` に残します。

## 手元で試す

Node.js 18 以上、Python 3、git と、PATH 上にある `testcase-lint` を使います。
導入方法は [testcase-lint のインストール手順](../../../testcase-lint/README.md#入れる)を参照してください。
リポジトリのルートから次を実行します。

```bash
cd tools/codd-agent/samples/test-maintenance
python3 src/discount.py 10000    # 割引後の金額 9000 を表示
testcase-lint                    # ケースを点検
testcase-lint run                # 単体テスト 2 件を実行
python3 verify-tests.py           # ケース一覧の生成 → 点検 → 実行
```

点検では入力エラーのケースがないという警告が出ます。この例は有効な金額の境界 2 件を扱い、点検は終了コード 0 で完了します。
関数を直接呼ぶ例なので、操作を始める場所を調べる規則 `X03` は無効にしています。
`verify-tests.py` はいずれかの工程が失敗した時点で非 0 を返します。
`TESTCASE_LINT` に実行ファイルのパスを設定すると、PATH 上の既定コマンドを上書きできます。

## 自分のプロジェクトに組み込む

対象リポジトリのルートに、必須の雛形として `testcase-lint.config.yaml`、`test-bindings.json`、`verify-tests.py` をコピーします。
unittest を使う場合は `run_unit.py` もコピーします。ケースとテストコードは、`tests/cases/` 配下のケースと `tests/test_discount.py` を雛形にするか、プロジェクトに既存のものを使います。

1. コピーしたケースとテストコードは、自分のプロジェクトの実装に合わせて置き換えます。
2. `test-bindings.json` のケースファイルと ID、テストのファイル・suite・selector を更新します。
3. `testcase-lint.config.yaml` でケースの場所と実行コマンドを更新します。`run_unit.py` は unittest の結果を標準出力の JUnit XML に変換する例です。別のランナーを使う場合は、そのランナーが JUnit XML を出力するコマンドに置き換えます。
4. 既存の `.gitignore` に `results/` と `__pycache__/` を追記します。結果の出力先を変えたら、`verify-tests.py` と codd-agent の設定も合わせます。

対象プロジェクトを git リポジトリにした上で、配布元のルートから初期化します。

```bash
python3 tools/codd-agent/init.py ~/work/my-app --side impl \
  --test "python3 verify-tests.py" --evidence "results/*.json" \
  --test-bindings test-bindings.json --test-catalogs results/cases.json
```

設定の見本は [codd-config.example.json](codd-config.example.json) にあります。
codd-agent が変更の確認時に渡す `TEST_EVIDENCE_RUN_ID` と `TEST_EVIDENCE_INPUTS_DIGEST` は、
`verify-tests.py` から `testcase-lint run` まで引き継がれます。
codd-agent はその値を使って、今回の入力で対応するテストが通ったことを照合します。

運用とトラブル対応は [テスト自動保守セットアップガイド](../../../../docs/guides/test-maintenance-setup.md)を参照してください。
