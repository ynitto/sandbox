# 往路で貼り付ける式

> Power Automate の実機では未確認。トリガーの出力パスとアクションの内部名を自分の環境で確かめて置き換える。

[往路フロー](flow.md) の順に「作成（Compose）」や「条件」に式を貼る。トリガー条件の欄だけ先頭の `@` も含めて貼る。[式のリファレンス](https://learn.microsoft.com/en-us/azure/logic-apps/expression-functions-reference)

## トリガー条件

`TEAM_ID`、`CHANNEL_ID`、`USER_ID_1`、`USER_ID_2` は実際の ID に差し替える。別のトリガーを使う場合は親投稿であることも確認し、返信が届くトリガーなら条件に親投稿の判定を加える。

~~~text
@and(
  equals(triggerBody()?['channelIdentity']?['teamId'], 'TEAM_ID'),
  equals(triggerBody()?['channelIdentity']?['channelId'], 'CHANNEL_ID'),
  contains(
    createArray('USER_ID_1', 'USER_ID_2'),
    coalesce(triggerBody()?['from']?['user']?['id'], '')
  )
)
~~~

## メッセージ ID と依頼 ID

「作成」`MessageId`。

~~~text
string(coalesce(triggerBody()?['id'], ''))
~~~

数字のみの ID を使う場合は「アレイのフィルター処理」`InvalidMessageIdChars` の入力を次の式にする。

~~~text
chunk(outputs('MessageId'), 1)
~~~

フィルターの詳細設定には次を貼る。

~~~text
@not(contains('0123456789', item()))
~~~

次の条件が真のときだけ「作成」`RequestId` に進む。最大長は `tm-` を含めて 128 文字に収めるためのもの。

~~~text
and(
  not(empty(outputs('MessageId'))),
  lessOrEquals(length(outputs('MessageId')), 125),
  empty(body('InvalidMessageIdChars'))
)
~~~

~~~text
concat('tm-', outputs('MessageId'))
~~~

数字以外を含む ID を扱う場合は、たとえば承認済みのハッシュ計算サービスに元の ID を UTF-8 として渡し、SHA-256 の 64 桁の 16 進文字列を「作成」`Sha256Hex` に入れる。Power Automate の標準式には SHA-256 関数がない。この方式では数字限定の条件に代えて、元の ID が空でなく、マーカー内で使える空白なしの値であることと、ハッシュ出力が仕様どおりであることを確認して次の式を `RequestId` に使う。

~~~text
concat('tm-', substring(toLower(outputs('Sha256Hex')), 0, 16))
~~~

## 投稿日時と本文

「作成」`PostedAt`。作成日時が空ならこの手順の前に終了する。`o` は ISO 8601 のラウンドトリップ形式で、Teams から来た UTC またはオフセット付きの作成日時を整形する。

~~~text
formatDateTime(triggerBody()?['createdDateTime'], 'o')
~~~

「作成」`RequestText` には HTML のとき次を貼る。「Html からテキスト」の内部名を `HtmlToText` とし、入力には投稿本文の HTML を指定する。プレーンテキストのときは、変換後の出力の代わりに本文の動的コンテンツを使う。

~~~text
trim(coalesce(body('HtmlToText'), ''))
~~~

## ファイル名と同名ファイルの検索

「作成」`RequestFileName`。

~~~text
concat(outputs('RequestId'), '.json')
~~~

`FindRequest` のフィルター クエリ。

~~~text
concat('FileLeafRef eq ''', outputs('RequestFileName'), '''')
~~~

`FindRequest` が成功した後、以下が真のときだけ作成に進む。

~~~text
empty(body('FindRequest')?['value'])
~~~

## 依頼 JSON

「作成」`RequestJson`。項目の値をオブジェクトに入れてから文字列化すると、本文の改行や引用符が JSON としてエスケープされる。

~~~text
setProperty(
  setProperty(
    setProperty(
      setProperty(
        setProperty(
          setProperty(
            setProperty(json('{}'), 'request_id', outputs('RequestId')),
            'team_id', triggerBody()?['channelIdentity']?['teamId']),
          'channel_id', triggerBody()?['channelIdentity']?['channelId']),
        'message_id', outputs('MessageId')),
      'author', triggerBody()?['from']?['user']?['displayName']),
    'posted_at', outputs('PostedAt')),
  'text', outputs('RequestText'))
~~~

「ファイルの作成」のファイル コンテンツ（UTF-8 の JSON）。

~~~text
string(outputs('RequestJson'))
~~~

宛先を固定するときは、上のファイル コンテンツの式を次に差し替える。`TARGET_NAME` はエージェントの宛先表にある名前。

~~~text
string(setProperty(outputs('RequestJson'), 'target', 'TARGET_NAME'))
~~~
