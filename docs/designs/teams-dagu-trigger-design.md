# Teams の投稿から Dagu を動かす設計（案）

Teams のメッセージを起点に、手元の Windows PC の Dagu のワークフローを動かし、結果を同じ Teams のスレッドに返す。
MS365 と手元の PC は、ネットワークの上ではどちらからも相手へ届かない。そこで逆向き（GitLab → Teams）と同じく、両方の世界に
もともとある「メール」を橋にする。ただし今回は、MS365 から GitLab へ書き込む口が無いので、**手元の PC がメールを取りに行く（pull）**。

状態: 案（2026-11-21）。実装はまだ無い。決めたことは §1、決めていないことは §9。

## 1. 前提（2026-11-21、利用者と確かめた）

| 項目 | 状況 | 効くところ |
|---|---|---|
| Power Automate のプレミアム（HTTP のアクション） | 使えない | MS365 から外へ HTTP を送れない。標準コネクタ（Teams・Office 365 Outlook）だけで組む |
| MS365 から GitLab が見えるか | 見えない | Power Automate からハブの issue へ直接書けない |
| GitLab の受信メール（メールで返信） | 使えない | メールを送って issue のコメントにする道も無い |
| OneDrive の同期クライアント | 使えない | Power Automate が置いたファイルを手元に下ろす道も無い |
| Dagu の置き場所 | Outlook と同じ Windows PC | 手元の Outlook を COM で読める |
| Outlook の版 | クラシック版 | COM（Outlook のオブジェクトモデル）が使える。新しい Outlook では使えない |
| Power Automate Desktop | 手元の PC にある（無料版） | 手元で Outlook のメールを読み、ファイルに書ける。無料版には定期実行もクラウドからの呼び出しも無い |

この条件で MS365 から手元へ届くのは、**手元の Outlook が受け取ったメール**だけになる。手元から GitLab へは今までどおり書ける。

## 2. 考えた経路と選んだもの

| 経路 | 判断 |
|---|---|
| Teams の送信 Webhook・ボット | 外から社内の口へ入る必要がある。使えない |
| Power Automate の HTTP で GitLab の API を叩く | プレミアムが無く、GitLab も見えない。使えない |
| メールを GitLab の受信アドレスへ送り、issue のコメントにする | GitLab の受信メールが無い。使えない |
| OneDrive にファイルを書き、同期クライアントで手元へ下ろす | 同期クライアントが無い。使えない |
| Playwright で Teams・Outlook の Web 画面を読む | 動くが、画面の変化で壊れやすく、社内ポリシーにも触れやすい。新しい Outlook に移らされたときの予備にする |
| **Power Automate が自分宛てにメールを送り、手元の Outlook を Power Automate Desktop で読む** | **選ぶ**。標準コネクタと社内で使える道具だけで済み、手元から外への通信も増えない。Power Automate Desktop を Dagu から起動できなければ、同じ読み取りを COM のスクリプトで行う（§4.1） |

## 3. 全体像

```
 Teams: メッセージの「…」→ ワークフロー「Dagu を動かす」（フォームで操作と DAG を選ぶ）
   │ Power Automate（標準コネクタだけ）
   ▼
 Outlook: 自分宛てのメール   件名 [dagu-req] <受付番号>   本文 JSON（§5）
   │ Outlook の仕分けルールで「dagu-req」フォルダへ
   ▼
 Dagu: teams-dagu-request（毎分起動。1 回の起動で 1 件）              ← 手元の Windows PC
   export   Power Automate Desktop のフロー「dagu-req の書き出し」を起動する      「dagu-req」の未読を 1 通 1 ファイルにして、メールを「処理済み」へ移す（§4.1）
   get      change-watch get --source teams-requests --consumer teams-dagu --one    フォルダの取得元。新しいファイルを 1 件受け取る
   check    change-watch --plugin outlook_mail check-run --change=${CHANGE}     許可リストと照らし合わせ、何を動かすかを出す
   ack      受け付けた旨を返す（下の「返す」と同じ道）
   run      dagu start <DAG> -- <引数>  /  dagu retry <DAG>  /  dagu status <DAG>
   reply    結果を返す
   commit   change-watch commit --source teams-requests --consumer teams-dagu ${CHANGE.key}
   │ 返す: 知らせを新しい issue にする例（change-watch-issue-hub-notice）を呼ぶ
   ▼
 GitLab: 知らせ用プロジェクトに issue を立てる → 「新しい issue」の通知メール
   ▼
 Power Automate（gitlab-mail-teams と同じ作り）: 本文の受付番号と返信先を読み、Teams の元のスレッドに返信する
```

戻りの道（手元 → GitLab → メール → Teams）は、gitlab-issue-hub-agent の知らせの例と gitlab-mail-teams のフローをそのまま使う。
作り足すのは、行きの道の「Teams → メール」（Power Automate）と「Outlook → ファイル」（手元の Power Automate Desktop）と、照合のコマンドだけ。

## 4. 分担

| 受け持ち | どこ | 何を |
|---|---|---|
| Teams の操作を受けてメールにする | Power Automate のフロー「Dagu を動かす」 | トリガー「選択したメッセージに対して」（Teams、標準）。フォームで操作と DAG を選ばせ、§5 の JSON を本文にして「メールの送信 (V2)」で自分宛てに送る。送る前に、押した人が許可した人かを見る |
| メールを仕分ける | Outlook の仕分けルール | 差出人が自分で、件名が `[dagu-req]` で始まるものを「dagu-req」フォルダへ移す。受信トレイを汚さない |
| メールをファイルにする | Power Automate Desktop のフロー「dagu-req の書き出し」（予備は `outlook_mail.py` の `export`。§4.1） | 「dagu-req」の未読を読み、1 通を 1 つのテキストファイル（§5）にする。既読にして「処理済み」フォルダへ移す。中身の判断はしない |
| 前回からの変化を返す・二重に動かさない | change-watch の `folder` 取得元 | ファイル 1 つが 1 件。`get --consumer` / `commit` で、同じ依頼を 2 回返さない |
| 何を動かしてよいかを決める | `outlook_mail.py` の `check-run` | 許可リスト（§6）と照らし合わせ、`route`（`start`・`retry`・`status`・`reject`）と、Dagu に渡す DAG の名前・引数を JSON で出す |
| 起動・分岐・再試行・履歴 | Dagu のワークフロー `teams-dagu-request` | §3 のステップ。条件は `preconditions` に書く |
| 結果を Teams に返す | 既存の `change-watch-issue-hub-notice` と、Teams に流す Power Automate のフロー | 知らせの issue の説明に受付番号と返信先を書く。フローがそれを読んで元のスレッドに返信する |

Outlook を読むのは書き出しの段だけにし、照合・起動・返信には Outlook を持ち込まない。照合（`check-run`）は change-watch の
プラグイン `outlook_mail.py` に置き、ほかのエージェントと同じく「ワークフローは change-watch を入り口にする」。change-watch の本体には手を入れない。

### 4.1 書き出しの作り方

**主: Power Automate Desktop（無料版）のフロー「dagu-req の書き出し」**

1. 「Outlook を起動します」（起動済みの Outlook につなぐ）
2. 「Outlook からメール メッセージを取得します」: フォルダ「dagu-req」、未読だけ、既読にする
3. 取れたメールごとに、件名から受付番号を取り出し、§5 の形の文を「テキストをファイルに書き込みます」で `<受信フォルダ>\<受付番号>.mail.txt` に書く
   （同じ名前のファイルがあれば書かない）
4. 「Outlook のメール メッセージを処理します」で「処理済み」フォルダへ移す

JSON を Power Automate Desktop の中で組まない。文字の逃がし（`"` や改行）を間違えやすいので、メールの事実を数行のテキストに書き、
JSON として読むのは `check-run`（Python。テストで固められる）に任せる。

起動のしかたは 2 つあり、どちらが使えるかは実機で確かめる（§9）。

| 起動 | しくみ | 注意 |
|---|---|---|
| A. Dagu から実行用の URL で起動する | `export` の段で `cmd /c start "" "ms-powerautomate:/console/flow/run?workflowName=dagu-req の書き出し"` を動かす。書き出しは非同期なので、同じ起動の `get` はまだ空のことがある。次の分の起動で拾われる | 起動のたびに確認の画面が出ないか。出るなら、出さない設定ができるか（社内の設定次第） |
| B. Power Automate Desktop のフローが自分で回る | フローの全体を「ループ」で包み、最後に 60 秒待つ。PC へのサインイン時にフローを起動する（スタートアップにショートカットを置く） | Dagu からは止まっていることが見えない。Dagu の `get` の段で、受信フォルダの印のファイル（フローが毎周書く）の更新時刻が古ければ失敗にして気づけるようにする |

**予備: `outlook_mail.py` の `export`（COM のスクリプト）**

A も B も使えないときに使う。やることは上の 1〜4 と同じで、同じ形のファイルを書く。COM の呼び出しを薄い層に閉じ込め、テストでは偽物の Outlook を差し込む。

## 5. 依頼のメール

件名は `[dagu-req] <受付番号>`。受付番号は Teams のメッセージ ID から作る（同じメッセージで 2 回押されたら同じ番号になる。
押し直しを別の依頼にしたいなら、押した時刻を足す。§9）。本文は JSON 1 つだけにする。

```json
{
  "v": 1,
  "id": "T1732150000000",
  "op": "start",
  "dag": "nightly-report",
  "params": {"DATE": "2026-11-20"},
  "by": {"name": "鈴木 一郎", "email": "ichiro.suzuki@example.co.jp"},
  "reply": {"team": "<チームの ID>", "channel": "<チャネルの ID>", "message": "<元のメッセージの ID>"},
  "text": "元のメッセージの本文（記録用。照合にも実行にも使わない）"
}
```

- Outlook は本文を HTML にすることがある。`check-run` は本文から最初の `{` から対応する `}` までを JSON として読み、読めなければ
  そのメールを `invalid` としてファイルにする（`check-run` が `reject` にし、差し戻しを返す）
- 本文の文字数の上限を決めておく（§9）。上限を超えたものは読まずに `invalid` にする

書き出しの段が書くファイル（`<受信フォルダ>/<受付番号>.mail.txt`、UTF-8）は、メールの側の事実を先頭の数行に書き、空行のあとに本文をそのまま置く。

```text
件名: [dagu-req] T1732150000000
差出人: me@example.co.jp
受信: 2026-11-21 10:15:02

{"v": 1, "id": "T1732150000000", "op": "start", …本文の JSON… }
```

change-watch の取得元 `teams-requests` は `folder`（`include: ["*.mail.txt"]`・`content: text`）にする。

## 6. 何を動かしてよいか（許可リスト）

Teams の本文はそのまま実行しない。動かせるのは、許可リストに書いた DAG と操作と引数だけにする。

```json
{
  "users": ["ichiro.suzuki@example.co.jp", "hanako.sato@example.co.jp"],
  "dags": {
    "nightly-report": {
      "label": "夜間レポート",
      "ops": ["start", "retry", "status"],
      "params": {"DATE": "^\\d{4}-\\d{2}-\\d{2}$"},
      "users": null
    },
    "cache-refresh": {"label": "キャッシュの作り直し", "ops": ["start", "status"], "params": {}}
  }
}
```

`check-run` は次をすべて満たすときだけ `start`・`retry`・`status` を出し、1 つでも外れれば `reject` と理由を出す。

1. メールの差出人が自分（`check-run` に渡す自分のアドレス）
2. `by.email` が `users`（DAG ごとの `users` があればそちら）に入っている
3. `dag` が `dags` にあり、`op` がその `ops` に入っている
4. `params` の名前がすべて許可リストにあり、値が正規表現に当たる（許可リストに無い引数は渡さない）
5. `v` が分かる版

Dagu へは `dagu start <DAG> -- 名前=値` の形で、引数の配列として渡す（シェルを通さない）。

最初に用意する操作は 3 つ。

| 操作 | すること | 返すもの |
|---|---|---|
| `start` | DAG を動かす | 受け付けたこと → 終わったら成否・所要時間・失敗したステップ |
| `retry` | 直近で失敗した回をやり直す | `start` と同じ |
| `status` | 直近の回の結果を見る | 成否・終わった時刻・失敗したステップ |

Teams のフォームの DAG の選択肢は、許可リストの `label` と同じものを Power Automate に書く。二重に持つことになるので、
`check-run` の側を正とし、フォームに無い DAG・許可リストに無い DAG はどちらも `reject` になる。

## 7. 決まりごと

- **二重に動かさない**: 同じ受付番号のファイルは同じ名前になり、change-watch の consumer が commit したものは返さない。
  書き出しの段は既にあるファイルを上書きしない
- **commit は必ず進める**: 照合で外れても、動かした DAG が失敗しても、返信のあとで commit する（`continue_on.failure`）。
  commit しないと毎分やり直し続ける
- **同時には 1 件**: `teams-dagu-request` は 1 回の起動で 1 件。動かした DAG が終わるまで次の起動は待つ（Dagu の既定の
  `maxActiveRuns: 1` で、重なった起動は飛ばされる）。長くかかる DAG があると、あとの依頼が待たされる（§9）
- **受け付けたことを先に返す**: `run` の前に `ack` を返す。終わるまで Teams に何も出ないと、押した人が押し直す
- **返す文に内部の綴りを出さない**: 返信は DAG の `label` と、成否・時刻・失敗したステップの名前で書く
- **行頭の `/`**: 返す文は GitLab の issue を通るので、行頭の `/` は `\/` にする（gitlab-issue-hub-agent と同じ）

## 8. 手元の PC の条件と、失敗したとき

| 条件・失敗 | 扱い |
|---|---|
| Dagu はログインしているユーザーとして動かす | Windows のサービスとして別のアカウントで動かすと、Power Automate Desktop も COM も Outlook に入れない。作業スケジューラなら「ユーザーがログオンしているときのみ実行」にする |
| Power Automate Desktop にサインインしておく | 無料版は、サインインしている人の画面の上でしか動かない（画面がロックされていても動くかは確かめる。§9） |
| Outlook を起動しておく | 閉じていると、書き出しの段が Outlook を起動しようとして遅い・止まることがある。書き出せなかったメールは未読のまま残り、次の回で拾われる |
| 「プログラムがアクセスしようとしています」の警告 | 社内の設定（ウイルス対策の状態・グループポリシー）で出る。出るなら情シスに確認する。出たまま自動では回せない |
| 「dagu-req」フォルダが無い | 書き出しの段が失敗する。README に仕分けルールとフォルダの作り方を書く |
| PC が寝ている・電源が切れている | メールは Outlook に溜まり、起きたあと古い順に 1 件ずつ処理される。古すぎる依頼を捨てる期限を決める（§9） |
| 新しい Outlook に切り替えられた | Power Automate Desktop の Outlook のアクションも COM も使えなくなる。書き出しの段だけを Playwright で Outlook の Web 画面を読むものに替える。§5 のファイルの形を変えなければ、ほかはそのまま使える |

## 9. 決めていないこと

| 項目 | 案 |
|---|---|
| 押した人を Power Automate で取れるか | 「選択したメッセージに対して」のトリガーが押した人を返すかを実機で確かめる。取れなければ、メッセージの投稿者で代える（その場合「投稿した人」が許可リストに入っている必要がある） |
| 押し直しを別の依頼にするか | 既定は同じ依頼（同じ受付番号）として捨てる。やり直したいときは `retry` を選ぶ |
| 古い依頼を捨てる期限 | 受信から 60 分を過ぎたものは動かさず、期限切れとして返す |
| 本文の上限 | 16 KB |
| Power Automate Desktop の起動のしかた | §4.1 の A（Dagu から実行用の URL）を先に試す。確認の画面が出て消せなければ B（フローが自分で回る） |
| 画面のロック中に動くか | 無料版の Power Automate Desktop が、ロックした画面の裏で Outlook のアクションを動かせるかを確かめる。動かなければ、ロック中は COM のスクリプトを使う |
| 長くかかる DAG | 最初は待つ（§7）。困ったら、`run` を `dagu enqueue` にして終わりを別のワークフローで見に行く形へ分ける |
| 返信の宛先 | 元のスレッドに返す。返せない（ID が無い）ときは決めたチャネルに投稿する |
| 名前 | 照合のプラグインを `outlook_mail`、エージェントを `outlook-mail-agent`（change-watch の `agents/` の下）とする |

## 10. 作る順

0. 手元の PC で確かめる: Power Automate Desktop で「dagu-req の書き出し」を組んで 1 通流す。PowerShell から実行用の URL で起動して確認の画面が出るかを見る（§4.1 の A か B かが決まる）
1. `outlook_mail.py` の `check-run`（`.mail.txt` の読み取り・本文の JSON・許可リスト）。Outlook に触れないので、Linux のテストで固められる
2. Power Automate Desktop のフローの手順書（§4.1）。A・B のどちらも使えなければ、予備の `export`（COM）を作る
3. Dagu のワークフローの例 `teams-dagu-request.yaml.example` と、change-watch の取得元 `teams-requests`（`folder`、`content: json`）の設定の例
4. Power Automate のレシピ 2 本: 行き（Teams → メール）と、戻り（知らせの issue のメール → Teams の元のスレッドへの返信）
5. 利用者向けの README（入れ方・Outlook の仕分けルールの作り方・許可リストの書き方・止まったとき）
