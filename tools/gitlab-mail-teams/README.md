# gitlab-mail-teams — GitLab の通知メールを Teams のカードに変換する

本番では Outlook が受信した GitLab 通知メールを Power Automate がパースし、Adaptive Card を組み立てて Teams に投稿する。受信から投稿までは MS365 内で完結する。

## 本番フロー

1. Outlook の新着メールをトリガーにし、GitLab の通知を差出人などで絞り込む。
2. Power Automate で件名・本文から通知の種類や項目を抽出し、Adaptive Card を組み立てる。
3. 同じフローから Teams のチャットまたはチャネルにカードを投稿する。投稿先の振り分けや `unknown` の扱いは `kind` を使った条件で設定できる。

設定手順は [Power Automate レシピ](power-automate/flow.md)、抽出式は [式の具体例](power-automate/expressions.md) を参照。

## 上流の仕組み ― ハブissue方式

対象には、[gitlab-issue-hub-agent](../change-watch/agents/gitlab-issue-hub-agent/README.md) が使うハブ issue へのコメント通知メールも含まれ得る。1 つの GitLab issue を介し、メール・Power Automate とローカル側がやりとりするハブissue方式が、ローカルと MS365 をつなぐ標準の橋となる。コメントのみの通知は通常 `unknown` として扱う。

## ローカル CLI の使い方

CLI はパース規則とカード設計をオフラインで試作・検証する道具。Python の標準ライブラリだけで動き、手元の `.eml` から生成したカード JSON をフローの投稿アクションに貼って試せる。[Dagu DAG 例](dags/gitlab-mail-teams.yaml) も、複数のメールでパース規則を試作・検証するためのローカル実行例。本番の変換・投稿は Power Automate が担う。

```sh
# 何の通知かを JSON で見る（既定）
python3 gitlab_mail_teams.py mail.eml

# フローで試す Adaptive Card の JSON を生成する
python3 gitlab_mail_teams.py mail.eml --format card

# 標準入力から読む（パスを省略するか - を書く）
cat mail.eml | python3 gitlab_mail_teams.py - --format card
```

出力は UTF-8、字下げ 2 の JSON。終了コードは 0 = 成功、2 = メールとして読めない（空・ヘッダーが無い・
ファイルが無い。理由は標準エラーに出る）。

### `--format json` — 通知の内容

```json
{
  "kind": "pipeline_failed",
  "project": "platform/order-service",
  "title": "Failed pipeline for main",
  "number": 30417,
  "actor": "Ichiro Suzuki",
  "url": "https://gitlab.example.co.jp/platform/order-service/-/pipelines/30417",
  "status": "failed",
  "branch": "main",
  "summary": "在庫引当のタイムアウトを 3 秒にする / 失敗したジョブ: test/unit-test, test/integration-test",
  "received_at": "2026-11-19T02:41:09+00:00",
  "subject": "order-service | Failed pipeline for main | 9f2c41d7",
  "sender": "GitLab <gitlab@gitlab.example.co.jp>"
}
```

| 項目 | 内容 |
|---|---|
| `kind` | 通知の種類（下の表） |
| `project` | プロジェクトのパス |
| `title` | Issue・マージリクエストの題名。パイプラインは件名の「Failed pipeline for main」の部分 |
| `number` | Issue・マージリクエストの番号。パイプラインはパイプラインの番号 |
| `actor` | その操作をした人の名前 |
| `url` | GitLab の該当ページ |
| `status` | `opened` / `review_requested` / パイプラインの状態（`failed`・`success` など） |
| `branch` | マージリクエストは元のブランチ、パイプラインは対象のブランチ |
| `summary` | 本文の要点（280 文字まで）。パイプラインはコミットメッセージと失敗したジョブ |
| `received_at` | メールの日時（ISO 8601） |
| `subject` / `sender` | メールの件名と差出人。どの種類でも必ず入る |

取れなかった項目は `null` になる。

| `kind` | どんな通知か |
|---|---|
| `issue_created` | Issue が作られた |
| `mr_review_requested` | マージリクエストのレビューを頼まれた（マージリクエストでメンションされた場合も含む） |
| `pipeline_failed` | パイプラインが失敗した |
| `unknown` | 上のどれでもない（成功したパイプライン、コメントの通知、GitLab 以外のメールなど）。取れた項目は埋まる |

### `--format card` — Teams のカード

Adaptive Card（バージョン 1.4）を 1 つ出す。見出し（通知の種類）、題名、項目の一覧（プロジェクト・
ブランチ・状態・操作した人・受信日時）、要点、「GitLab で開く」ボタンで組む。パイプラインの失敗だけ
見出しが警告色になる。URL が取れなかったメールではボタンを付けない。

## テスト

```sh
cd tools/gitlab-mail-teams
python3 -m unittest discover -s tests
```

`tests/fixtures/` に、3 種類の通知メールの見本がある。

## 制約

- **読むのは GitLab が英語で書く定型文**（`created an issue`・`requested a new review`・`Commit Message:`
  など）と、通知メールに付く `X-GitLab-*` ヘッダー。GitLab の版や表示言語の設定で文面が違うと、項目が
  `null` になったり `unknown` に落ちたりする。見本は GitLab の通知メールの形に合わせて書いたもので、
  社内の GitLab が実際に送ったメールではない。使い始める前に実物の `.eml` を数通通して確かめる。
- 分類するのは上の 3 種類だけ。それ以外はすべて `unknown`（カードにはなるので、投稿するかどうかは
  Power Automate の側で `kind` を見て決める）。
- 要点は本文の頭 280 文字で、Markdown の記号はそのまま残る。添付ファイルは見ない。

## 既存ツールとの違い

| ツール | 入力 | 出力 | 役目 |
|---|---|---|---|
| `issue-mailbox` | GitLab の API を定期的に問い合わせる | tmux で動いているエージェントへの連絡 | Issue をエージェントに届ける |
| `copilot365-bridge` | 手元からの問いかけ | MS365 Copilot の回答 | MS365 Copilot を呼び出す |
| **`gitlab-mail-teams`** | GitLab の通知メール（`.eml`） | Teams のカードの JSON | メールの変換だけ。どこにも接続しない |

`issue-mailbox` は開発側で GitLab の API を使って人やエージェントに知らせる。本番フローは GitLab の通知メールを使って MS365 側の Teams に知らせる。
