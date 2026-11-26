# 社内ソフトウェアファクトリーの導入手順

> 作成 2026-11-26 ／ 状態：導入手順案  
> 2026-11-27: ファクトリーの対象を GitHub から社内のセルフホスト GitLab へ訂正。  
> 本書は sandbox リポジトリ（GitHub）で管理し、本書の変更も GitHub の PR で扱う。  
> 構成と状態遷移は [ソフトウェアファクトリーの設計](./2026-11-26-software-factory-design.md) を参照。

## TL;DR

- 対象リポジトリを 1 つ選び、GitLab CI パイプライン、社内プロキシ、goalie 経由のローカル Copilot アプリ、Dagu の順に接続を確認する。
- 低リスクな Issue に `factory` を付けて、ドラフト MR、CI、ローカル AI レビュー、人のマージまで通す。その後、修正タスクと振り返りを順番に追加する。
- 停止時は Dagu の新規起動を止め、対象 Issue から `factory` を外し、実行中の job の完了を待つ。終了コード 4 の依頼は受け箱に残る。

## 1. はじめに

社内導入担当者が、Matt Pocock の用語集に基づく「チケット・MR・スケジュールを入力として、マージ済みのコードを生産するエージェントの仕組み」を段階的に立ち上げるための手順。既製のサービスを使い、ファクトリー固有の仕事はタスクに表す。各 run は人の介入を待たずに終わる。実行環境は使い捨てとし、業務状態はチケットトラッカー・Git ホスト・オーケストレーターに残す。

| 段階 | 確認すること |
| --- | --- |
| Phase 0 準備 | 認証、CI、受け箱、手動 poll が通る |
| Phase 1 最小開始 | 低リスクな Issue を人のマージまで通せる |
| Phase 2 拡張 | MR 上で修正し、更新後に CI とレビューをやり直せる |
| Phase 3 育てる | 繰り返す指摘をスキルやマニュアルの改善につなげられる |

sandbox（GitHub）の PR #1123（goalie 統合）はマージ済みで、`tools/goalie/` は main に存在する。残る未検証範囲は実 GitLab・実 Copilot アプリ・実 Dagu での動作のみ。`tools/software-factory/` 配下の controller・設定・雛形は設計上の実装予定資産であり、以下は各 Phase の必要資産が揃った時点で使う。操作例は Windows の PowerShell を想定する。Kiro CLI は将来の代替候補。

## 2. Phase 0 準備

### 目的

社内 GitLab の対象プロジェクトとローカル PC を接続し、スケジュールを有効にする前に手動で確認できる状態を作る。

### 手順

1. 社内のセルフホスト GitLab で対象プロジェクトを 1 つ作成または選定し、Issues と GitLab CI が利用できることを確認する。プロジェクトの「Manage → Labels」で次のラベルを作る。既存ラベルは用途を確認して再利用する。`factory` は Issue の対象指定と MR の印に共用し、進捗ラベルは Issue に付ける。[GitLab のラベル設定](https://docs.gitlab.com/user/project/labels/)

   - `factory`
   - `factory:implementing`
   - `factory:mr-open`
   - `factory:in-review`
   - `factory:reviewed`
   - `factory:failed`

2. GitLab の設定画面で既定ブランチを保護ブランチ（Protected branches）にする。「Allowed to push and merge」を「No one」にし、「Allowed to merge」をマージを担当する人のロールに限定する。MR のマージ設定で「Pipelines must succeed」を有効にし、スキップしたパイプラインを成功扱いにする設定は無効にする。自動マージは使わない。`.gitlab-ci.yml` に MR パイプラインを設定し、テスト用のドラフト MR でもパイプラインが実行されることを確認する。[保護ブランチ](https://docs.gitlab.com/user/project/repository/branches/protected/) [MR のマージ条件](https://docs.gitlab.com/user/project/merge_requests/auto_merge/) [MR パイプライン](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/)

3. 対象プロジェクトのプロジェクトアクセストークンを発行し、`api` スコープと、Issue の更新、MR の作成・コメント、作業ブランチの push、パイプラインの状態確認に必要な最小のロールを割り当てる。個人アクセストークンを使う場合は、対象プロジェクトにアクセスを限定した専用アカウントで発行する。手元と Dagu の実行環境にトークンを `GITLAB_TOKEN` として注入し、設定ファイルには書かない。`GITLAB_URL` には疎通確認用に社内 GitLab のベース URL を設定し、後述の `factory.json` の `gitlab_url` にも同じ URL を設定する。社内プロキシに合わせて `HTTPS_PROXY`、必要に応じて `HTTP_PROXY` と `NO_PROXY` を設定する。[GitLab のプロジェクトアクセストークン](https://docs.gitlab.com/user/project/settings/project_access_tokens/)

   手元と Dagu の実行環境の両方で、プロキシ経由で GitLab REST API v4 に接続できることを確認する。

   ```powershell
   $env:GITLAB_URL = 'https://gitlab.example.com' # 社内 GitLab の URL に置き換える
   $projectId = [uri]::EscapeDataString('internal-group/internal-repo')
   curl.exe --fail --silent --show-error --request GET `
     --header "PRIVATE-TOKEN: ${env:GITLAB_TOKEN}" `
     --url "$($env:GITLAB_URL.TrimEnd('/'))/api/v4/projects/$projectId"
   ```

   プロジェクトパスの `/` は `%2F` にエンコードされる。HTTP 200 が返り、レスポンスの `path_with_namespace` が対象プロジェクトと一致することを確認する。[GitLab Projects API](https://docs.gitlab.com/api/projects/)

4. Python 3.9 以上を使えるローカル PC に `tools/goalie/` 一式を配置する。対象リポジトリを `C:/work/factory/internal-repo/` にクローンし、Copilot デスクトップアプリでそのフォルダをプロジェクトとして開く。受け箱 `.copilot-jobs/` はコミットの対象から外し、書き込みは依頼者と実行に必要な仕組みに限定する。
5. 次のコマンドが出すプロンプトを、同じプロジェクトを対象とするローカルの自動実行に貼る。推奨は 1 時間間隔と `--stay 50`。PC とアプリを起動した状態で、一度手で自動実行する。空の受け箱で「なし」と答えて終わることを確認する。居座りを有効にした自動実行は待機後に終わるため、すぐ確認する場合は初回だけ `--stay` を省いたプロンプトを使う。

   ```powershell
   python C:/tools/goalie/goalie.py job prompt --dir C:/work/factory/internal-repo --stay 50
   ```

6. フックを生成する。`.github/hooks/goalie.json` の `preToolUse` と `policy.json` の `tools` を確認する。アプリから作業フォルダ外に検証用ファイルを作るよう依頼し、書き込みが拒否されてファイルが作られず、作業フォルダの `.goalie/hook.jsonl` に `deny` が増えることを確認する。拒否が記録されない場合は、プロジェクトのフックが読み込まれているか [goalie README](../../tools/goalie/README.md) に沿って確認してから先へ進む。

   ```powershell
   python C:/tools/goalie/goalie.py hooks --dir C:/work/factory/internal-repo
   ```

7. 実装済みの `tools/software-factory/` を配置し、`factory.py` と同じディレクトリに下記の `factory.json` を置く。Dagu はインストール済みを前提とし、利用中の Dagu が参照する DAG の置き場へ `tools/software-factory/dags/factory-poll.example.yaml` をコピーする。Python と `factory.py` のパスを直し、`poll_interval_sec` が 300 秒なら `*/5 * * * *` にする。手動確認が終わるまではスケジュールを停止しておく。

   同じ PC で change-watch を使って GitLab を見ているなら、`factory.json` に `change_watch` を書いて問い合わせをまとめる（[README](../../tools/software-factory/README.md) の「GitLab への問い合わせを change-watch にまとめる」）。このとき接続先とトークンは change-watch の接続設定が持ち、`gitlab_url` は要らない。
8. `C:/work/factory/smoke.md` に「ファイルを変更せず、疎通確認として OK と報告してください」と UTF-8 で保存する。アプリで自動実行が動く状態で手動 job を投入する。続いて、`factory` 対象の Issue がない状態で手動 poll と状態表示を確認する。

   ```powershell
   python C:/tools/goalie/goalie.py job run --dir C:/work/factory/internal-repo --prompt-file C:/work/factory/smoke.md --name factory-smoke --timeout 7200
   python C:/tools/software-factory/factory.py poll
   python C:/tools/software-factory/factory.py status
   ```

### 設定例

```json
{
  "gitlab_url": "https://gitlab.example.com",
  "repositories": ["internal-group/internal-repo"],
  "label": "factory",
  "poll_interval_sec": 300,
  "max_concurrent": 1,
  "work_root": "C:/work/factory",
  "goalie_py": "C:/tools/goalie/goalie.py",
  "job_timeout_sec": 7200,
  "review": {"enabled": true},
  "journal": "C:/work/factory/journal.jsonl"
}
```

`gitlab_url`、`repositories`（`group/project` の配列）、`work_root`、`goalie_py`、`journal` と DAG の実行パスを環境に合わせる。クローンの配置は `<work_root>/<repo名>/`、ジャーナルは `work_root` 配下とする。初期値は 300 秒、同時実行 1 件。レビューは例では有効だが、初回の実装と CI の確認を先に行うなら Phase 1 の途中まで `review.enabled` を `false` にする。

### 完了条件

プロキシ経由の API 疎通、ドラフト MR でのパイプライン実行、既定ブランチの保護とパイプライン成功必須の設定、フックの拒否を確認できた。手動の `goalie.py job run` が終了コード 0 で終わり、手動の `factory.py poll` がエラーなく終わって `journal.jsonl` が作られた。

### 止め方・戻し方

DAG のスケジュールを停止したままにする。検証 job が残っていれば完了を待ち、結果を確認してから検証ファイルを片付ける。認証や保護ルールの設定を変更した場合は、導入前の設定を控えておき、試験専用の変更だけを戻す。

## 3. Phase 1 最小開始（Start slow）

### 目的

1 リポジトリ、同時実行 1 件で、低リスクな Issue を人のマージまで通す。初回は文書の誤記修正など、変更範囲と合否が明確な作業を選ぶ。

### 手順

1. 下の例にならって Issue を起票し、`factory` を付ける。人が付ける進捗用のラベル操作は対象指定だけとし、その後の進捗ラベルは controller に任せる。
2. Dagu のスケジュールを有効にする。ポーリングで `factory:implementing` になり、`tasks/implement.md` から作った依頼が `factory-impl-<Issue番号>` として投入されることを確認する。ドラフト MR のブランチが `factory/issue-<Issue番号>` で、MR に `factory` と Issue へのリンクがあり、Issue が `factory:mr-open` になることを確認する。
3. GitLab の MR 画面で、最新コミットに対する GitLab CI パイプラインの状態を確認する。保留中は `factory:mr-open` のまま待つ。失敗時は `factory:failed` と理由を確認する。
4. `factory.json` の `review.enabled` を `true` にする。最新コミットの GitLab CI パイプラインが成功すると `factory:in-review` となり、`tasks/review.md` から作った `factory-review-<MR番号>` がローカル Copilot アプリで差分ファイルだけを読み、MR にレビュー結果が付くことを確認する。
5. 「重大」はマージ前に人が修正する。「指摘」は人が対応要否を判断する。「提案」は任意として扱う。修正でコミットが増えたら、最新の CI とレビューを確認する。`factory:failed` の再開操作は運用担当者と確認し、未解決の依頼が残る場合は再投入しない。Issue が `factory:reviewed` になったら、人が MR のドラフトを解除し、内容を確認してマージする。Issue がクローズされたことも確認する。

### 設定例

`factory.json` は `"repositories": ["internal-group/internal-repo"]`、`"label": "factory"`、`"max_concurrent": 1`、`"review": {"enabled": true}` で開始する。

#### Issue テンプレートと記入例

```markdown
## 目的
セットアップ手順にある設定ファイル名の誤記を直す。

## 変更範囲
README.md のセットアップ節にある設定ファイル名 1 箇所。

## 受け入れ条件
記載が実際のファイル名 factory.json と一致し、GitLab CI パイプラインが成功する。

## 対象外
設定ファイル自体、実行コード、他の節の変更。
```

### 完了条件

低リスクな Issue が数件、実装 → ドラフト MR → GitLab CI パイプラインの成功 → ローカルレビュー → 人のマージまで通り、Issue のクローズを確認できた。

### 止め方・戻し方

全体の新規実行を止めるには Dagu の DAG スケジュールを停止する。対象を外すには Issue の `factory` を外す。レビューだけを止めるには `review.enabled` を `false` にする。進行中の依頼は [トラブル時の止め方](#7-トラブル時の止め方) に沿って扱う。

## 4. Phase 2 拡張（修正タスク）

### 目的

対応対象として合意したレビュー指摘を、既存のドラフト MR への修正コミットにつなげる。

### 手順

1. 修正タスクが実装された版を用意し、対象にする指摘、試行上限、人に引き継ぐ条件を決める。CI 失敗も修正タスクへ回す場合は、扱う失敗の範囲を先に決める。
2. `factory.json` に下記設定を追加し、Phase 1 と同じリポジトリで試す。指摘の対象リビジョンを確認し、既存の作業ブランチに修正コミットが追加されることを確認する。最新のコミットで CI とローカルレビューを再実行してから人がマージを判断する。
3. 安定したら、次の GitLab プロジェクトに Phase 0 の準備を行う。プロジェクトごとに controller の配置先（`factory.json` を含む）、作業ルート、Dagu の実行環境を分ける。それぞれの `repositories` に対象プロジェクトのパスを設定し、対応する `GITLAB_TOKEN` を渡す。新しいプロジェクトでも低リスクな Issue から始める。
4. 異なる作業フォルダで並行実行でき、PC の負荷、滞留、タイムアウトに問題がなければ `max_concurrent` を 1 件ずつ増やす。同じ作業フォルダでは引き続き 1 件ずつ処理する。

### 設定例

既存の `factory.json` のトップレベルに `"fix": {"enabled": true}` を追加する。試行中は `"max_concurrent": 1` とする。

### 完了条件

指摘から修正コミットまで進み、更新後の CI とレビューを確認できた。試行上限を超えた場合や解決できない場合に、人への引き継ぎが確認できた。

### 止め方・戻し方

`"fix": {"enabled": false}` に戻すと新規の修正投入が止まる。進行中の修正 job の完了を待ち、追加されたコミットを人が確認する。並行実行に問題があれば `max_concurrent` を 1 に戻す。

## 5. Phase 3 育てる（振り返りタスク）

### 目的

マージ済み MR から繰り返し発生するレビュー指摘を見つけ、スキルやマニュアルの改善案を作る。

### 手順

1. 対象リポジトリと集計期間を決め、マージ済みの `factory` MR に紐づくレビュー指摘を集める。MR 番号、指摘の分類、重要度、対応結果を追えるようにする。
2. まず手動で振り返りタスク（Retro）を試し、繰り返した指摘を分類する。改善提案に根拠となる MR、変更先のスキルやマニュアル、追加する説明や例をまとめる。
3. 定期運用に移す場合は、実装済みの Retro タスクを別の Dagu スケジュールで動かす。担当者が提案をレビューし、採用した案を通常の変更手順で取り込む。次回の振り返りで、同じ指摘が続くか確認する。
4. 新しい困りごとが出たら、入力と成果物を決められる単位でタスクを一つずつ足す。

### 設定例

運用設定の例として、毎週月曜 09:00（Dagu の運用タイムゾーン）に、`internal-group/internal-repo` で直近 7 日にマージされた `factory` MR を集計する。報告先は運用担当者が確認する GitLab Issue とし、同種の指摘が複数の MR に出たときに改善案を添える。Retro の設定スキーマは導入時に決める。

### 完了条件

集計結果から根拠の MR にたどれ、採用した改善案をスキルまたはマニュアルに反映できた。次の振り返りで反映後の指摘を確認できた。

### 止め方・戻し方

Retro 用の Dagu スケジュールを停止する。確認中の提案は保留し、反映済みの変更を戻す必要がある場合は通常の手順で変更をレビューする。

## 6. 日常運用

- 人の仕事はチケットを書くことと、レビュー・マージを判断すること。[Issue テンプレートと記入例](#issue-テンプレートと記入例) を使い、変更範囲と終了条件を明確にする。
- 現在状態の正は GitLab の Issue ラベル。MR のドラフト・マージ状態と最新の CI 結果を合わせて見る。コメントは進捗・結果報告として読む。
- `python C:/tools/software-factory/factory.py status` で対象と保留理由を確認する。詳細は `Get-Content C:/work/factory/journal.jsonl -Tail 20` で、対象番号・実行 ID・遷移・終了コードを追う。ジャーナルは履歴と二重投入防止にも使う。

## 7. トラブル時の止め方

1. Dagu の運用画面またはスケジューラ設定で対象 DAG の定期起動を停止し、次の起動が発生しないことを確認する。画面や停止操作は利用版の実環境で確認する。この時点では開始済みの実行を強制終了しない。
2. 新規対象から外す Issue の `factory` を除去する。controller は書き込み前に対象ラベルを再確認する。開始済みの job がある場合はラベルを外した後も結果を照合する。
3. 実行中の job の完了を待つ。`python C:/tools/goalie/goalie.py job status --dir C:/work/factory/internal-repo` と Dagu のログ、ジャーナル、GitLab の MR を突き合わせる。コード 0 は成功、1 はアプリの失敗報告で `factory:failed`、2 は使い方の誤りで controller が停止して人に通知する。コード 3 は未回収で取り下げ済みのため、再開後に再投入できる。コード 4 は着手済みで依頼が残るため、`factory:failed` にして、再投入や同じ仕事の手動実行を避ける。
4. 再開前に保留の原因を解消し、受け箱の実行中依頼、ジャーナルの未解決実行、ブランチと MR を照合する。`factory:failed` の解除手順の詳細は未決のため、失敗記録だけを消しての再開は避ける。安全に再開できる Issue に `factory` を戻し、DAG を止めた状態で手動 poll を一度実行する。重複がなく状態が整合していれば定期起動を戻す。
