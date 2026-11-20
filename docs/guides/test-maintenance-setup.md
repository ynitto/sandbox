# テスト自動保守セットアップガイド — ケースの点検から個別テストの実行確認まで

> 対象は、自然文のテストケースを実際のテストに結び付け、変更時にその実行を codd-agent で確認したいプロジェクトです。  
> 関連資料は [割引計算の実働サンプル](../../tools/codd-agent/samples/test-maintenance/) と [テスト自動保守の計画](../plans/2026-10-25-project-independent-test-maintenance.md) です。

## 0. 点検と実行で確かめること

| 操作 | 結果の `purpose` | 確かめること |
|---|---|---|
| `testcase-lint` | `check` | 自然文ケースの書き方や対応先 |
| `testcase-lint run` | `execution` | 実際に動いた個別テストの成否 |

点検に通っても、実装が期待どおりに振る舞うかは個別テストの実行で確かめます。
codd-agent は対応表と今回の実行結果を照合し、変更の影響を受けるケースの確認漏れを検出します。

## 1. 前提条件

- Node.js 18 以上と npm
- Python 3（`python3` コマンド）
- git と、対象プロジェクトの git リポジトリ
- PATH 上の `testcase-lint`。入れ方は [testcase-lint のインストール手順](../../tools/testcase-lint/README.md#入れる)を参照してください。

## 2. 導入する

### 2.1 雛形をコピーしてケースを書く

[割引計算の実働サンプル](../../tools/codd-agent/samples/test-maintenance/) から、対象リポジトリのルートに次のファイルをコピーします。

- 必須の雛形は `testcase-lint.config.yaml`、`test-bindings.json`、`verify-tests.py` です。設定と対応表はプロジェクトに合わせて更新します。
- `run_unit.py` は unittest を使う場合にコピーします。
- `tests/cases/` 配下のケースとテストコード（サンプルでは `tests/test_discount.py`）は、プロジェクトのケースとテストに置き換えます。既存のケースやテストがあれば、それを使います。
- `.gitignore` の除外行 `results/` と `__pycache__/` は、対象リポジトリの `.gitignore` に追記します。

`tests/cases/` にケースを書き、条件、操作、観測できる期待値を具体的に記述します。
ケースの配置を変える場合は `testcase-lint.config.yaml` の `cases` を変更し、`bindings`、`evidence`、`run` で指定するパスも合わせて見直します。

### 2.2 対応表を書く

`test-bindings.json` の `version` は 1 です。各 `bindings` 要素の `case` にケースファイルと ID を書き、
`targets` に対応するテストの `suite`、`file`、`selector` を書きます。
パスはリポジトリのルートからの相対パスです。
ケース一覧（カタログ）に載るケース ID はすべて対応表に書きます。未対応の ID があると、codd-agent が実行結果と照合できません。
サンプルでは `DISC-01` と `DISC-02` の両方を `unit` の個別テストに対応させています。
`testcase-lint.config.yaml` の `run.unit.command` は、プロジェクトのテスト結果を JUnit XML で出力するコマンドに合わせます。

### 2.3 点検してテストを実行する

対象リポジトリのルートで実行します。

```bash
testcase-lint
testcase-lint run
```

点検の終了コードと `results/case-check.json` を確認してから、`results/unit.json` で目的のテストが `passed` になったことを確かめます。
続けて `python3 verify-tests.py` を実行し、終了コードが 0 で、ケース一覧（`results/cases.json`）も生成されることを確かめます。

### 2.4 codd-agent に登録する

配布元のルートから、対象リポジトリのパスを指定して実行します。

```bash
python3 tools/codd-agent/init.py ~/work/my-app --side impl \
  --test "python3 verify-tests.py" --evidence "results/*.json" \
  --test-bindings test-bindings.json --test-catalogs results/cases.json
```

`verify-tests.py` は `testcase-lint -f json -o results/cases.json` でケース一覧を作り、点検とテスト実行を順に行います。
どれかが失敗すると非 0 で終了します。
変更を確認するときに codd-agent が渡す `TEST_EVIDENCE_RUN_ID` と `TEST_EVIDENCE_INPUTS_DIGEST` は、
子プロセスまでそのまま引き継ぎます。手動実行では、一覧と結果を生成できることを確かめられます。

## 3. 変更が入ったとき

1. 設計・実装の差分と依頼を見て、変更する振る舞いと維持する振る舞いを計画に記録します。
2. 対応表と影響範囲から関連ケース・テストを探し、追加・更新・廃止・維持を判断します。設計変更では根拠に沿って期待結果を更新し、リファクタでは期待結果を維持します。仕様の判断が必要なときは仕様を確認します。
3. ケースを点検し、必要なテストと対応表を更新します。codd-agent の変更確認で個別テストの結果を照合します。不具合修正では、追加したテストが修正前に意図した理由で失敗し、修正後に通ることも確認します。
4. 変更した仕様・ケース・テストと、実行した確認・未確認事項を記録します。

詳しくは [計画の §4「変更時の流れ」](../plans/2026-10-25-project-independent-test-maintenance.md#4-変更時の流れ)を参照してください。

## 4. 定期運用

- ケース点検とテスト実行を定期化する場合は、点検に失敗しても実行結果を残せる [testcase-lint の Dagu 例](../../tools/testcase-lint/dags/testcase-lint.example.yaml) を使えます。
- codd-agent の定期点検には [codd-lint の Dagu 例](../../tools/codd-agent/dags/codd-lint.example.yaml) を使えます。結果は対象リポジトリの `.codd/lint.md` で確認します。

両方を同じ作業コピーで動かす場合は、作業ディレクトリと PATH を設定し、`results/` への同時書き込みを避けるように実行順を決めます。
codd の定期点検も設定済みの `test` を実行するため、必要に応じて実行を一方に集約します。
変更の厳密な実行確認には、codd-agent がその変更の確認時に発行した実行識別子を持つ結果を使います。

## 5. つまずきどころ

| 症状・操作 | 確認すること |
|---|---|
| テストを動かしたのに対応が見つからない | `suite` とファイルのパスを確認し、`selector` をランナーが出力する個別テスト名と完全一致させます。 |
| 結果ファイルが作業差分に出る | ケース一覧と結果を置く `results/` を git 管理外にします。 |
| 対応をすべて廃止する | 廃止を計画に記載し、`"bindings": []` を持つ対応表ファイルを残します。 |
| 点検は通ったが実行を確認できない | 点検の結果（`purpose: check`）だけでは実行確認になりません。`testcase-lint run` で `purpose: execution` の結果を生成します。 |
| 旧形式の結果で確認できない | `purpose` がない旧結果は厳密な実行確認の証拠になりません。現在の実行コマンドで結果を生成します。 |
| 結果が未確認になる | `run_id` または `inputs_digest` が今回の値と合わない結果は未確認になります。環境変数の引き継ぎを確認して再実行します。 |

## 6. 完了チェックリスト

- [ ] ケースに ID、条件、操作、観測できる期待結果がある。
- [ ] 対応表の ID・パス・suite・selector が実際のケースとテストに一致する。
- [ ] 点検が終了コード 0 で完了し、`purpose: check` の結果がある。
- [ ] テスト実行で対応する個別テストが通り、`purpose: execution` の結果がある。
- [ ] ケース一覧と結果の生成先を git の管理外にしている。
- [ ] codd-agent の変更確認で、対象ケースの個別テストが今回の実行結果で確認できる。
