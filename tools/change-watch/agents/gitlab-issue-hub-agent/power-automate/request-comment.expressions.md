# 依頼コメントを作る式

式は Power Automate の実機では未確認。「Html からテキスト」のアクション名を `Html_to_text`、行頭の `/` を置き換える「作成」の名前を `Safe_comment` とした例なので、フローで使う名前に合わせる。

## 種別

```text
if(or(contains(triggerOutputs()?['body/subject'],'集計'),contains(triggerOutputs()?['body/subject'],'レポート'),contains(triggerOutputs()?['body/subject'],'報告')),'report',
 if(or(contains(triggerOutputs()?['body/subject'],'アカウント'),contains(triggerOutputs()?['body/subject'],'権限')),'account',
  if(or(contains(triggerOutputs()?['body/subject'],'問い合わせ'),contains(triggerOutputs()?['body/subject'],'問合せ'),contains(triggerOutputs()?['body/subject'],'質問')),'inquiry','')))
```

## 種別があるときのコメント

```text
concat('【依頼】[', variables('種別'), '] ', triggerOutputs()?['body/subject'], decodeUriComponent('%0A'),
       '差出人: ', triggerOutputs()?['body/from'], decodeUriComponent('%0A'),
       '受信日時: ', convertFromUtc(triggerOutputs()?['body/receivedDateTime'],'Tokyo Standard Time','yyyy-MM-dd HH:mm'), decodeUriComponent('%0A'),
       '受付番号: M', convertFromUtc(triggerOutputs()?['body/receivedDateTime'],'Tokyo Standard Time','yyyyMMddHHmmss'), decodeUriComponent('%0A'),
       if(equals(variables('種別'),'inquiry'), decodeUriComponent('%0A'), ''),
       body('Html_to_text'))
```

## 種別が空のときのコメント

```text
concat('【未分類】', triggerOutputs()?['body/subject'], decodeUriComponent('%0A'), body('Html_to_text'))
```

## 行頭の `/` を効かなくする（`Safe_comment`）

```text
replace(
  replace(replace(variables('コメント'), decodeUriComponent('%0D%0A'), decodeUriComponent('%0A')), decodeUriComponent('%0D'), decodeUriComponent('%0A')),
  concat(decodeUriComponent('%0A'),'/'), concat(decodeUriComponent('%0A'),decodeUriComponent('%5C%2F')))
```

## HTTP に渡す本文

```text
setProperty(json('{}'), 'body', outputs('Safe_comment'))
```
