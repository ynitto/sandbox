# 復路フローの組み方

> Power Automate の実機では未確認。動的コンテンツの名前と出力の構造は、自分の環境で確かめて [式](expressions.md) を合わせる。

## 1. GitLab の通知メールを受け取る

Office 365 Outlook の「新しいメールが届いたとき (V3)」で、監視するメールフォルダを指定し、詳細設定の「差出人」に GitLab の通知アドレスを設定する。通知を専用フォルダに振り分ける場合は、実際にそのフォルダへの配信で起動することを確かめる。

## 2. 本文から相関マーカーを読む

トリガーの「本文」全体を `MailBody` に保存し、HTML なら「Html からテキスト」で `BodyText` を作る。テキスト本文ならそのまま `BodyText` に使う。次の形式を探す。

~~~text
<!-- teams-bridge:request=<request_id> teams-message=<message_id> -->
~~~

HTML コメントはテキスト変換で消える場合があるため、変換前の本文にも同じマーカーを探す。エスケープされたコメントは、テキスト変換で上記の文字列になれば抽出できる。**どちらの本文にもマーカーが無いメールは対象外として終了する。** [抽出式](expressions.md#相関マーカーの抽出) で両 ID を取り出す。閉じ記号の欠落や ID の形式違反があれば終了する。issue 通知とコミット通知の両方で、GitLab が実際に送る本文にマーカーが残ることを確かめる。

## 3. SharePoint から返信先を引く

マーカーにあるのは `request_id` と `message_id` で、Graph の返信にはチーム ID とチャンネル ID も要る。

1. SharePoint の「ファイルの取得（プロパティのみ）」を `FindRequest` として追加する。往路と同じサイト、ライブラリ、依頼フォルダを指定し、「入れ子になった項目を含める」は「いいえ」、上位件数は 1、フィルター クエリは [照合式](expressions.md#依頼ファイルの検索と読み込み) を使う。
2. 成功時に結果が 0 件なら、返信先を特定できないため終了する。検索に失敗した場合も投稿に進まない。結果があれば最初の項目の「識別子」を SharePoint の「ファイル コンテンツの取得」`ReadRequest` に渡す。読み込み前に削除された場合も投稿に進まない。
3. UTF-8 の JSON を `RequestJson` に読み、`team_id` と `channel_id` を取得する。ファイル内の `request_id`・`message_id` がマーカーと一致し、チーム ID とチャンネル ID がある場合だけ進む。依頼フォルダの書き込み権限は往路の接続などに限定する。

## 4. Teams の元の投稿に返信する

Microsoft Entra ID（Azure AD）にアプリを登録し、Microsoft Graph の **委任されたアクセス許可** `ChannelMessage.Send` に必要な同意を付与する。対象チャンネルに返信できる職場アカウントで OAuth 2.0 の認可コード フローを使い、Graph 用の委任アクセストークンを用意する。たとえばアプリ登録と OAuth 2.0 を設定したカスタム コネクタで同じ Graph POST を実行すれば、接続でサインインとトークン更新を扱える。汎用の「HTTP」アクションを使う場合は、委任トークンを扱える認証構成から渡し、実行履歴でトークンを公開しない。クライアント資格情報で得られるアプリ専用トークンでは通常の返信を投稿できない。`Teamwork.Migrate.All` は移行用。[Graph の返信 API と権限](https://learn.microsoft.com/en-us/graph/api/chatmessage-post-replies?view=graph-rest-1.0)

| HTTP の項目 | 設定 |
|---|---|
| メソッド | `POST` |
| URI | `https://graph.microsoft.com/v1.0/teams/{team-id}/channels/{channel-id}/messages/{message-id}/replies` |
| Authorization | `Bearer <Graph の委任アクセストークン>`（接続で付与する場合は接続に任せる） |
| Content-Type | `application/json` |
| 本文 | [Graph の URI と本文の式](expressions.md#graph-の-uri-と本文) で作ったオブジェクト |

本文の例。件名を短い要約に使い、通知から取り出した社内 GitLab の issue またはコミットへのリンクを付ける。

~~~json
{
  "body": {
    "contentType": "html",
    "content": "<p>集計結果の issue が作成されました。</p><p><a href=\"https://gitlab.example.com/group/project/-/issues/42\">GitLab で開く</a></p>"
  }
}
~~~

成功時は `201 Created` が返る。Teams コネクタの「メッセージで返信する」アクションが使える環境では、同じチーム、チャンネル、親メッセージ ID を指定してもよい。

## 5. 同じ通知メールでの二重返信を防ぐ

例として、別の SharePoint リストに処理記録を置く。1 つのメールボックスを処理するフローで、Outlook の `Internet Message Id` をキーにする。実際の出力名と、再処理で値が同じことを確認する。キー列には一意の値を強制し、空や列に収まらない ID は処理を止める。

1. Graph に送る直前に、キーと状態 `pending` の項目を作る。同じキーがすでにある場合は返信せずに終了する。その他の記録失敗時も投稿に進まない。
2. POST の自動再試行を無効にし、`201 Created` を受けたら返信 ID と状態 `sent` を記録する。
3. POST がタイムアウトしたり、投稿後に記録更新が失敗したりした場合は `pending` を残す。スレッドを確認して、返信済みなら `sent` に更新し、未送信を確認できた場合にだけ記録を解除して再処理する。Graph の POST 自体は冪等ではなく、同じ内容を再送すると別の返信になり得る。

マーカーなし、依頼ファイルなし、同じ通知の再処理をそれぞれ試し、返信が増えないことを確かめる。
