# ソフトウェアファクトリー Phase 1

Dagu から定期実行するコントローラーが、社内セルフホスト GitLab の `factory` ラベル付き Issue を処理します。実装とレビューは goalie の受け箱を通じてローカルの Copilot アプリに依頼します。コントローラーは Python 3.9 以上の標準ライブラリで動作します。GitLab REST API v4 への問い合わせは、change-watch に任せる（推奨）か、`urllib` で直接呼び出します。タイトルに `Draft:` 接頭辞を付けてドラフト MR を作成し、GitLab CI の結果を確認してレビュー結果を MR のノートに投稿します。マージは人が判断して行います。

このツール自体の開発・単体テスト CI は sandbox（GitHub）で行います。

## 構成

| ファイル | 役割 |
| --- | --- |
| `factory.py` | CLI、クライアント、状態遷移、ジャーナル |
| `factory.example.json` | `init` が書き出す設定の雛形 |
| `tasks/implement.md`、`tasks/review.md` | Copilot アプリへの依頼文の雛形 |
| `dags/factory-controller.example.yaml` | 5 分ごとにポーリングする Dagu の設定例 |
| `change-watch.example.yaml` | GitLab の問い合わせを change-watch に任せるときの取得元の例 |
| `test/test_factory.py` | 外部コマンドやネットワークを使わない単体テスト |
| `test/test_change_watch.py` | 本物の change-watch を偽の GitLab につないで確かめる結合テスト |

## セットアップ

1. 対象リポジトリの作業フォルダを用意し、`origin` とローカルの既定ブランチを確認します。Copilot アプリでそのフォルダを開き、goalie の受け箱を処理するように設定します。
2. GitLab の接続先を `GITLAB_URL` または設定ファイルの `gitlab_url` で指定します。対象プロジェクトで Issue と MR を操作できる API トークンを `GITLAB_TOKEN` に設定します（`GL_TOKEN` も使用可）。作業フォルダの Git は `origin` にブランチを push できるように設定し、対象ラベルと、下表の `factory:*` 進捗ラベルを各プロジェクトに作ります。
3. `init` で設定を作成して環境に合わせて編集し、`status` で対象を確認します。DAG 例のパスとスケジュールを調整して Dagu に登録します。

詳細は `docs/plans/2026-11-26-software-factory-introduction.md`を参照してください。

## 設定

| 項目 | 設定内容 |
| --- | --- |
| `repositories` | GitLab プロジェクトパスの空でない配列（例 `group/project`、`group/sub/project`）。末尾のプロジェクト名は一意にする |
| `gitlab_url` | GitLab のベース URL。省略時は環境変数 `GITLAB_URL` を使う。`change_watch` を書くときは不要 |
| `change_watch` | GitLab への問い合わせを change-watch に任せるときに書く。下の「GitLab への問い合わせを change-watch にまとめる」 |
| `label` | 対象を示すラベル。雛形では `factory` |
| `poll_interval_sec` | Dagu のスケジュールを合わせるための目安（秒）。コントローラー自体は参照しない |
| `max_concurrent` | 全リポジトリにまたがる未解決の実行の上限。作業フォルダごとの上限は 1 件 |
| `work_root` | 作業フォルダの親。既定の作業フォルダは `<work_root>/<リポジトリ名>`。ロックもこの配下に置く |
| `work_dirs` | リポジトリごとの作業フォルダ上書き。省略可 |
| `goalie_py` | `goalie.py` のパス |
| `job_timeout_sec` | goalie に投入してからタイムアウトとするまでの秒数 |
| `review.enabled` | CI 成功後にレビューを依頼するか |
| `fix.enabled` | Phase 2 用。Phase 1 では設定を読み取り、状態遷移には使わない |
| `journal` | 追記型の JSONL ジャーナルのパス |

## GitLab への問い合わせを change-watch にまとめる

`gitlab_url` で直接つなぐと、5 分ごとの poll のたびに Issue と MR の一覧を取り直し、CI を待つ MR は 1 件につき 3 回問い合わせます。`change_watch` を書くと、GitLab の読み取りと書き込みはすべて change-watch を通ります。

| 問い合わせ | change-watch を通すと |
| --- | --- |
| `factory` の Issue と MR の一覧 | change-watch の取得元の予定（例では 15 分ごと）でだけ取得し、ほかの poll は保存済みの一覧で答える |
| CI の結果と MR の head | CI を待つ MR だけを 1 件ずつ取り直し、`ci_max_age_sec` のあいだ使い回す。レビュー結果を投稿する直前だけは、その場で取り直す |
| ラベルの付け替え・MR の作成 | 応答を一覧に入れるので、次の取得を待たずに次の poll から反映される。ラベルは付け外しだけを送り、人が付けたほかのラベルを上書きしない |
| コメント・差分・既定ブランチ | 同じ接続と同じ要求の間隔で 1 回ずつ送る。取得が失敗した後の待ちも、同じ GitLab を見るほかの取得元と分け合う |

1. change-watch を入れる（`python tools/change-watch/install.py install`）。
2. `change-watch.example.yaml` の `sources` を change-watch の設定に足し、プロジェクトのパスを直す。同じ GitLab を見るほかの取得元と同じ設定ファイルにまとめると、要求の間隔と失敗の待ちを分け合える。
3. connections.yaml に GitLab の URL とトークンを書く（`token: ${GITLAB_TOKEN}` なら、トークンは今までどおり環境変数で渡せる）。
4. `factory.json` に次を足す。

```json
"change_watch": {
  "command": ["python", "C:/Users/you/AppData/Local/Programs/change-watch/change-watch.pyz"],
  "config": "C:/Users/you/.config/change-watch/change-watch.yaml",
  "connections": "C:/Users/you/.github/connections.yaml",
  "ci_max_age_sec": 240
}
```

| 項目 | 設定内容 |
| --- | --- |
| `command` | change-watch を起動するコマンド（文字列か配列） |
| `config` / `connections` | change-watch の設定ファイルと接続設定のパス |
| `sources` | リポジトリごとの取得元の名前（`{"group/project": {"issues": "...", "merge_requests": "..."}}`）。リポジトリが 1 つなら省略でき、`factory-issues` と `factory-mrs` を使う |
| `ci_max_age_sec` | CI を待つ MR を取り直す間隔（秒）。既定 240。poll の間隔より短くすると、poll ごとに 1 回取り直す |

すべての `factory` の Issue を見る取得元を別の用途ですでに持っているなら、`sources.issues` にその名前を書いてもかまいません。`factory` ラベルの絞り込みはコントローラー側でも行います。

## 使い方

リポジトリのルートから実行する例です。`--config` を省略すると `factory.py` と同じフォルダの `factory.json` を使います。

```sh
python tools/software-factory/factory.py init
python tools/software-factory/factory.py poll --config path/to/factory.json
python tools/software-factory/factory.py run-once --config path/to/factory.json
python tools/software-factory/factory.py status --config path/to/factory.json
```

`init` で既存ファイルを上書きする場合は `--force` を指定します。`poll` は 1 サイクル実行し、別名の `run-once` も完全に同じ動作をします。DAG 例では `poll` を使用します。`status` は Issue のラベル、MR・CI、直近のイベント、未解決の実行、設定による保留を表示する読み取り専用のコマンドです。終了コードは正常時が 0、設定や使い方の誤りが 2、その他のエラーが 1 です。

## 状態遷移

| Issue の進捗ラベル | コントローラーの処理 |
| --- | --- |
| 進捗ラベルなし | 枠が空いていれば実装を投入し、`factory:implementing` にする |
| `factory:implementing` | 実装ジョブの完了を待つ間はこの状態を維持する。完了を確認したサイクルで成果のブランチ、コミット、追跡対象ファイルの状態を確認し、ドラフト MR を作成または再利用して `factory:mr-open` にする |
| `factory:mr-open` | CI が未完了なら待機する。失敗なら MR にノートを投稿して `factory:failed`、成功なら `factory:in-review` に進む。レビュー無効時は直接 `factory:reviewed` にする |
| `factory:in-review` | レビュージョブの完了を待つ間はこの状態を維持する。完了を確認したサイクルで、同じ head で CI が成功していればレビューを投稿し、重大な指摘があれば `factory:failed`、無ければ `factory:reviewed` にする。head が変わった場合は `factory:mr-open` に戻す |
| `factory:reviewed` / `factory:failed` | head が変わり、未解決の実行や受け箱に残る依頼がなければ `factory:mr-open` に戻す |

CI ゲート（ブレーキ1）は GitLab CI の設定が前提です。`skipped` は成功扱いとし、パイプラインが無い場合は `pending` のまま待機します。

進捗ラベルは Issue ごとに常に最大 1 個です。付け替える際は旧ラベルを外します。対象ラベル（`label`）は人が外すまで残ります。Phase 1 のレビュー報告は、対象 SHA と判定を表示でき、承認や変更要求の権限を前提としない MR のノートを採用しています。

### goalie の結果

| 結果 | 処理 |
| --- | --- |
| 0 相当 | 実装またはレビューの結果を適用する |
| 1 相当 | Issue に失敗の要旨をコメントし、`factory:failed` にする |
| 投入時の 2 | Issue にコメントし、設定ファイルが変わるまで新規投入を保留する。結果回収と CI 照合は続ける |
| 3 相当 | 未着手の依頼を取り下げ、次のサイクル以降に実装またはレビューを再投入できる状態に戻す |
| 4 相当 | `factory:failed` にし、Issue に受け箱の確認を依頼する。依頼ファイルが残る作業フォルダへの新規投入は待つ |
| `missing` | 結果不明として `factory:failed` にし、Issue に受け箱の確認を依頼する |

## 検証状況と今後

`python -m unittest discover -s tools/software-factory/test` で単体テストと change-watch との結合テストを実行できます。実 GitLab、実 Copilot アプリ、実 Dagu での連携は未検証です。Phase 2 以降の修正依頼に向けて `fix.enabled` を用意しています。
