# 復路で貼り付ける式

> Power Automate の実機では未確認。トリガーの出力パスとアクションの内部名は自分の環境で確かめて置き換える。

[復路フロー](flow.md) の順に式タブに貼る。各名前は「作成（Compose）」の内部名の例。「アレイのフィルター処理」の詳細設定には先頭の `@` も含める。

## 本文の準備

`MailBody`。本文プレビューではなく全文を使う。

~~~text
coalesce(triggerBody()?['body'], '')
~~~

`BodyText`。HTML の場合は `MailBody` を「Html からテキスト」`HtmlToText` に渡し、次の式を使う。プレーンテキストなら `outputs('MailBody')` を使う。

~~~text
coalesce(body('HtmlToText'), '')
~~~

## 相関マーカーの抽出

`MarkerPrefix`。

~~~text
'<!-- teams-bridge:request='
~~~

`MarkerSource`。変換前にリテラルのマーカーがある場合は、コメントを保持している変換前の本文を使う。

~~~text
if(contains(outputs('MailBody'), outputs('MarkerPrefix')), outputs('MailBody'), outputs('BodyText'))
~~~

`MarkerStart`。

~~~text
indexOf(outputs('MarkerSource'), outputs('MarkerPrefix'))
~~~

`MarkerStart` が負なら対象外として終了する。0 以上の分岐にだけ `MarkerTail` を置く。

~~~text
substring(
  outputs('MarkerSource'),
  add(outputs('MarkerStart'), length(outputs('MarkerPrefix')))
)
~~~

`MarkerEnd`。

~~~text
indexOf(outputs('MarkerTail'), ' -->')
~~~

`MarkerEnd` が 0 以下なら終了する。正の場合だけ `MarkerParts` を作る。

~~~text
split(substring(outputs('MarkerTail'), 0, outputs('MarkerEnd')), ' teams-message=')
~~~

`length(outputs('MarkerParts'))` が 2 のときだけ、次の順で `RequestId` と `MessageId` を作る。

~~~text
first(outputs('MarkerParts'))
~~~

~~~text
last(outputs('MarkerParts'))
~~~

「アレイのフィルター処理」`InvalidRequestIdChars` の入力を `chunk(outputs('RequestId'), 1)` にし、詳細設定には次を貼る。

~~~text
@not(contains('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-', item()))
~~~

次の条件が真のときだけ SharePoint へ進む。このレシピの往路が付ける接頭辞 `tm-` を確認する。メッセージ ID は依頼ファイルの値と照合してから使う。

~~~text
and(
  startsWith(outputs('RequestId'), 'tm-'),
  greater(length(outputs('RequestId')), 3),
  lessOrEquals(length(outputs('RequestId')), 128),
  empty(body('InvalidRequestIdChars')),
  not(empty(outputs('MessageId'))),
  not(contains(outputs('MessageId'), ' ')),
  not(contains(outputs('MessageId'), decodeUriComponent('%0D'))),
  not(contains(outputs('MessageId'), decodeUriComponent('%0A'))),
  not(contains(outputs('MessageId'), decodeUriComponent('%09')))
)
~~~

## 依頼ファイルの検索と読み込み

`FindRequest` のフィルター クエリ。

~~~text
concat('FileLeafRef eq ''', outputs('RequestId'), '.json''')
~~~

`FindRequest` が成功し、次が真のときだけ `ReadRequest` に進む。「識別子」は検索結果の先頭から選び、内部キーが `{Identifier}` の環境では `first(body('FindRequest')?['value'])?['{Identifier}']` を使える。

~~~text
not(empty(body('FindRequest')?['value']))
~~~

`RequestJson`。SharePoint がバイナリを `$content` に Base64 で返す場合の式。本文がすでに JSON 文字列なら `json(body('ReadRequest'))` を使う。

~~~text
json(base64ToString(body('ReadRequest')?['$content']))
~~~

返信先の照合条件。

~~~text
and(
  equals(outputs('RequestJson')?['request_id'], outputs('RequestId')),
  equals(outputs('RequestJson')?['message_id'], outputs('MessageId')),
  not(empty(outputs('RequestJson')?['team_id'])),
  not(empty(outputs('RequestJson')?['channel_id']))
)
~~~

## Graph の URI と本文

HTTP の URI。チャンネル ID などはパスの区切りとは別にエンコードする。

~~~text
concat(
  'https://graph.microsoft.com/v1.0/teams/',
  encodeUriComponent(outputs('RequestJson')?['team_id']),
  '/channels/',
  encodeUriComponent(outputs('RequestJson')?['channel_id']),
  '/messages/',
  encodeUriComponent(outputs('MessageId')),
  '/replies'
)
~~~

`GitLabUrl` は、通知の結果リンクを取り出した「作成」とする。次は HTML 変換後の本文で URL が独立した単語として残る場合の例。「アレイのフィルター処理」`ResultLinks` の入力で、改行・タブ・括弧を空白にして分割する。

~~~text
split(
  replace(replace(replace(replace(replace(
    outputs('BodyText'),
    decodeUriComponent('%0D'), ' '),
    decodeUriComponent('%0A'), ' '),
    decodeUriComponent('%09'), ' '),
    '(', ' '),
    ')', ' '),
  ' '
)
~~~

詳細設定。ホストとプロジェクトは実際の許可先に差し替える。複数のプロジェクトを扱う場合は、許可した接頭辞を条件に追加する。

~~~text
@or(
  startsWith(item(), 'https://gitlab.example.com/group/project/-/issues/'),
  startsWith(item(), 'https://gitlab.example.com/group/project/-/commit/')
)
~~~

結果があるときだけ `first(body('ResultLinks'))` を `GitLabUrl` に入れる。実際の通知で結果リンクが最初の候補になることを確かめる。変換後の本文に URL が残らない場合は、元の HTML から結果リンクの `href` を取り出し、同じ許可先を照合する。結果リンクを特定できない場合は、投稿前に終了して実行履歴で確認する。

`Summary` はメール件名を最大 280 文字にした例。件名を参照する動的コンテンツは環境に合わせる。

~~~text
slice(coalesce(triggerBody()?['subject'], 'GitLab からの結果通知'), 0, 280)
~~~

`ReplyHtml`。メール由来の件名をテキストとして埋め込み、リンクの属性値も HTML 用にエスケープする。

~~~text
concat(
  '<p>',
  replace(replace(replace(outputs('Summary'), '&', '&amp;'), '<', '&lt;'), '>', '&gt;'),
  '</p><p><a href="',
  replace(replace(replace(replace(outputs('GitLabUrl'), '&', '&amp;'), '"', '&quot;'), '<', '&lt;'), '>', '&gt;'),
  '">GitLab で開く</a></p>'
)
~~~

HTTP の本文。

~~~text
setProperty(
  json('{"body":{}}'),
  'body',
  setProperty(json('{"contentType":"html"}'), 'content', outputs('ReplyHtml'))
)
~~~
