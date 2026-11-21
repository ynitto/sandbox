# change-watch エージェントの eval

固定入力と期待値を使って、ci-triage の診断と mr-review の IaC 検査の挙動を検証します。エージェントや組み込みパターン、判定ルールを変更する前後に手動で実行し、既知のケースで後退が起きていないかを確認できます。終了コードを使って、将来 CI にも組み込めます。

対象は決定的なルールベース処理です。ci-triage は組み込みパターンを使います。サイト別のパターン上書きは対象外です。proof プラグインやレビュー文生成など、LLM を必要とする処理は出力が非決定的なため対象外です。

## 実行方法

リポジトリルートで実行します。

```sh
python3 tools/change-watch/eval/run_eval.py
python3 tools/change-watch/eval/run_eval.py --target ci-triage
python3 tools/change-watch/eval/run_eval.py --target iac
```

対象ごとの pass/fail 件数を表示し、失敗ケースには期待値と実際の値を表示します。失敗が1件でもあれば終了コードは 1、全ケースが成功すれば 0 です。

変更前にベースラインを保存し、変更後の実行で比較します。

```sh
python3 tools/change-watch/eval/run_eval.py --save-baseline /tmp/change-watch-baseline.json
python3 tools/change-watch/eval/run_eval.py --baseline /tmp/change-watch-baseline.json
```

ベースラインには対象・ケースごとの `status` と `actual` を保存します。比較時は fail から pass になったケースを「改善」、pass から fail になったケースを「後退」、同じステータスのケースを「不変」と表示します。今回のケースに対応する記録がなければ「比較対象なし」と表示します。終了コードは今回の結果で決まります。

## ケースの追加

`cases/<target>/<case-name>/` を作成し、UTF-8 の `input.json` と `expected.json` を置きます。ランナーは `input.json` があるサブディレクトリを名前順に処理します。

- **ci-triage** の入力には `pipeline`（`id`、`project_id`、`sha`、`ref`、`source`、`web_url`）、`jobs`（`id`、`name`、`stage`、`failure_reason`、`web_url` を持つオブジェクトの配列）、`traces`（ジョブ ID を文字列キーとするトレースのマップ）を指定します。期待値には全体の `category` と、検出したパターン ID を重複なくソートした `patterns` を指定します。未マッチの場合は `patterns` を空配列にします。
- **iac** の入力には `diffs`（`new_path`、`old_path`、unified diff 文字列の `diff` を持つオブジェクトの配列）と、パスからファイル内容を引く `files` を指定します。参照するファイル内容がなければ `files` は空オブジェクトにできます。期待値には検出したルール ID を重複なくソートした `rules` を指定します。検出なしの場合は空配列にします。

## ランナーのテスト

```sh
python3 -m unittest discover -s tools/change-watch/eval/tests -t tools/change-watch/eval
```
