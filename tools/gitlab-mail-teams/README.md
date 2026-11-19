# gitlab-mail-teams — GitLab の通知メールを Teams のカードに変換する

GitLab が送ってくる通知メール（`.eml`）を読み、何の通知かと要点を取り出して、Microsoft Teams に
投稿できる Adaptive Card の JSON にする変換器。Python の標準ライブラリだけで動く。

## 目的

社内の GitLab は Teams から見えず、GitLab から Teams へ直接通知を送る経路もない。一方で
**GitLab の通知メールは Outlook に届く**。このメールを「Issue ができた」「レビューを頼まれた」
「パイプラインが落ちた」という形の整ったデータに直せば、Power Automate で Teams のチャネルへ
カードとして流せる。

このツールが受け持つのは**メール 1 通 → JSON 1 つ**の変換だけ。GitLab にも Teams にも接続しない。
メールを受け取るところと Teams に投稿するところは Power Automate が受け持つ。

## 社内での組み立て方

```
GitLab ──通知メール──▶ Outlook
                         │  ① Power Automate「新しいメールが届いたとき」
                         │     （差出人が GitLab のものだけ）→ メールを .eml で書き出し、
                         ▼     OneDrive の inbox フォルダに保存
                   inbox/*.eml        ← OneDrive の同期で手元の PC にも現れる
                         │  ② 手元の PC で Dagu が 5 分おきに変換（dags/gitlab-mail-teams.yaml）
                         ▼
                   cards/*.json       ← OneDrive の同期でクラウドへ戻る
                         │  ③ Power Automate「ファイルが作成されたとき」
                         ▼     → ファイルの内容を「チャットまたはチャネルにカードを投稿する」に渡す
                      Teams のチャネル
```

1. **メールを `.eml` にする（Power Automate）** — 「新しいメールが届いたとき」をトリガーにし、差出人を
   GitLab の通知アドレスに絞る。「メールのエクスポート」で得た内容を、OneDrive の受信用フォルダに
   `<メッセージ ID>.eml` として保存する。
2. **変換する（手元の PC）** — [`dags/gitlab-mail-teams.yaml`](dags/gitlab-mail-teams.yaml) を Dagu に置く。
   受信用フォルダの `.eml` を 1 通ずつ変換し、カードの JSON を出力用フォルダに書く。変換できたメールは
   `done`、読めなかったメールは `failed` に移すので、同じメールを二度投稿しない。
3. **Teams に投稿する（Power Automate）** — 出力用フォルダの「ファイルが作成されたとき」をトリガーにし、
   ファイルの内容をそのまま「チャットまたはチャネルにカードを投稿する」のカード欄に渡す。

通知の種類で投稿先を分けたいときは、`--format json` の出力も一緒に書き出し、Power Automate の条件で
`kind` を見て分ける。

## 使い方

```sh
# 何の通知かを JSON で見る（既定）
python3 gitlab_mail_teams.py mail.eml

# Teams に投稿するカードの JSON にする
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
- メールを取りに行かないし、Teams にも投稿しない。同じメールを二度渡せば同じカードが二度出る
  （重複を防ぐのは、処理済みのメールを移すワークフローの役目）。
- 出すのはカードそのもの。Teams の Webhook に直接送る場合は、送り先が求める外側の包み
  （`attachments` の配列など）を送る側で足す。
- 要点は本文の頭 280 文字で、Markdown の記号はそのまま残る。添付ファイルは見ない。

## 既存ツールとの違い

| ツール | 入力 | 出力 | 役目 |
|---|---|---|---|
| `issue-mailbox` | GitLab の API を定期的に問い合わせる | tmux で動いているエージェントへの連絡 | Issue をエージェントに届ける |
| `copilot365-bridge` | 手元からの問いかけ | MS365 Copilot の回答 | MS365 Copilot を呼び出す |
| **`gitlab-mail-teams`** | GitLab の通知メール（`.eml`） | Teams のカードの JSON | メールの変換だけ。どこにも接続しない |

`issue-mailbox` は GitLab の API に届く場所（開発側）で人やエージェントに知らせる。このツールは
GitLab の API に届かない Teams の側へ、メールという既にある経路を使って知らせる。
