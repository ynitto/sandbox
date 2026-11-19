# Power Automate の式の具体例

[フロー構成](flow.md) の抽出手順。Outlook トリガーからは X-GitLab-* ヘッダーを参照できない前提で、CLI の件名・本文による判定を再現する。ヘッダーだけにある通知理由や番号には差が出る。各ブロックを式タブに貼り、見出しや説明にある名前で「作成（Compose）」や変数を用意する。「アレイのフィルター処理」の詳細設定を使う場合は式の先頭に @ を付ける。変数を更新するループは逐次実行する。[Microsoft Learn の式リファレンス](https://learn.microsoft.com/en-us/azure/logic-apps/expression-functions-reference)

## 入力と改行

Subject、BodyText、FromDisplay を文字列として初期化する。BodyText は HTML をテキスト化した本文で、リンク先 URL も残す。値が欠けた場合は coalesce(値, '') で空文字を渡す。Compose「LF」と「Lines」を順に作る。

~~~
decodeUriComponent('%0A')
~~~

~~~
split(replace(replace(variables('BodyText'), decodeUriComponent('%0D%0A'), outputs('LF')), decodeUriComponent('%0D'), outputs('LF')), outputs('LF'))
~~~

## 署名の除去

Lines を逐次処理し、次の条件が初めて真になった行から後ろを除く。残した行を配列 BodyCore に入れ、Compose「BodyCoreLower」は toLower(join(variables('BodyCore'), outputs('LF'))) とする。

~~~
or(
  equals(trim(item()), '--'),
  startsWith(item(), 'View it on GitLab'),
  startsWith(item(), 'You''re receiving this email because')
)
~~~

## 件名、末尾参照、タイトル

CleanSubject を trim(variables('Subject')) で初期化する。「Do Until」で次の終了条件を使う。先頭のコロンより前を判定するため、大文字小文字やコロン前の空白も扱える。

~~~
not(and(
  contains(variables('CleanSubject'), ':'),
  contains(createArray('re', 'fwd', 'fw'), toLower(trim(first(split(variables('CleanSubject'), ':')))))
))
~~~

ループ本体は終了条件が偽の場合だけ、次の Compose の結果で CleanSubject を更新する。

~~~
trim(slice(variables('CleanSubject'), add(indexOf(variables('CleanSubject'), ':'), 1)))
~~~

split(variables('CleanSubject'), ' | ') を「選択（Select）」に渡し、テキストモードで trim(item()) を適用する。その出力（body('Segments') とする）の last() を Compose「LastSeg」に入れる。

RefMark と RefDigits は空文字で初期化する。LastSeg が ) で終わり、lastIndexOf(outputs('LastSeg'), '(') が 0 以上のとき、Compose「RefToken」を作る。

~~~
slice(outputs('LastSeg'), add(lastIndexOf(outputs('LastSeg'), '('), 1), -1)
~~~

slice(outputs('RefToken'), 0, 1) が # または ! の場合、slice(outputs('RefToken'), 1) を候補の数字列とする。chunk(候補, 1) を「アレイのフィルター処理」に渡し、次の条件で数字以外を検出する。候補が空でなく、フィルター結果が空で、isInt(候補) が真の場合だけ RefMark と RefDigits を設定し、number に int(variables('RefDigits')) を使う。

~~~
not(contains('0123456789', item()))
~~~

Compose「TitleParts」は先頭のプロジェクト名を除いた配列。単一セグメントの件名では元の配列を使う。

~~~
if(greater(length(body('Segments')), 1), skip(body('Segments'), 1), body('Segments'))
~~~

RefMark が空でない場合のみ、末尾要素を trim(slice(outputs('LastSeg'), 0, lastIndexOf(outputs('LastSeg'), '('))) に置き換える。前の要素は take(outputs('TitleParts'), sub(length(outputs('TitleParts')), 1)) で取り、順に配列へ追加する。join(配列, ' | ') を trim すると title が得られる。パイプラインの title は次節の式で上書きする。

## パイプライン・MR・Issue の判定

Segments をフィルターし、次の条件に合う最初の項目を使う。

~~~
or(
  startsWith(toLower(item()), 'failed pipeline for '),
  startsWith(toLower(item()), 'successful pipeline for '),
  startsWith(toLower(item()), 'fixed pipeline for ')
)
~~~

フィルター結果が空でない場合だけ、最初の項目を Compose「PipelineSeg」に入れる。Compose「PipelineBranch」は次の式で求め、空であれば通常の件名として扱う。この例は GitLab の標準的な空白区切りを前提とする。

~~~
first(split(trim(slice(outputs('PipelineSeg'), add(indexOf(outputs('PipelineSeg'), ' for '), 5))), ' '))
~~~

PipelineBranch が空でなければ IsPipeline を true にし、次の式で title を組み立てる。

~~~
concat(slice(outputs('PipelineSeg'), 0, add(indexOf(outputs('PipelineSeg'), ' for '), 5)), outputs('PipelineBranch'))
~~~

PipelineStatus は次の式で設定する。IsPipeline は初期値 false、PipelineStatus は初期値を空文字とする。

~~~
if(startsWith(toLower(outputs('PipelineSeg')), 'failed '), 'failed', 'success')
~~~

Compose「BodyLower」は toLower(variables('BodyText'))。Compose「BodyCoreLower」は署名を切った本文を小文字化したもの。IsMrReview と IsIssueCreated は次の式で求める。

~~~
and(
  equals(variables('RefMark'), '!'),
  or(
    contains(outputs('BodyLower'), 'requested review'),
    contains(outputs('BodyLower'), 'requested a new review'),
    contains(outputs('BodyLower'), 'requested you to review'),
    contains(outputs('BodyLower'), 'was requested to review')
  )
)
~~~

~~~
and(
  equals(variables('RefMark'), '#'),
  or(
    contains(outputs('BodyCoreLower'), 'created an issue'),
    contains(outputs('BodyCoreLower'), 'created a issue'),
    contains(outputs('BodyCoreLower'), 'created an new issue'),
    contains(outputs('BodyCoreLower'), 'created a new issue'),
    contains(outputs('BodyCoreLower'), 'opened an issue'),
    contains(outputs('BodyCoreLower'), 'opened a issue'),
    contains(outputs('BodyCoreLower'), 'opened an new issue'),
    contains(outputs('BodyCoreLower'), 'opened a new issue'),
    contains(outputs('BodyCoreLower'), 'issue was created'),
    contains(outputs('BodyCoreLower'), 'issue was opened')
  )
)
~~~

部分一致は CLI の単語境界チェックの近似。件名に番号があり、本文にも該当する定型句があるときに分類する。パイプライン判定を優先する。Compose「Kind」と「Status」を順に作る。

~~~
if(variables('IsPipeline'),
  if(equals(variables('PipelineStatus'), 'failed'), 'pipeline_failed', 'unknown'),
  if(outputs('IsMrReview'), 'mr_review_requested', if(outputs('IsIssueCreated'), 'issue_created', 'unknown')))
~~~

~~~
if(variables('IsPipeline'), variables('PipelineStatus'),
  if(equals(outputs('Kind'), 'mr_review_requested'), 'review_requested',
    if(equals(outputs('Kind'), 'issue_created'), 'opened', null)))
~~~

## URL、プロジェクト、パイプライン番号

本文全体を使う。join(outputs('Lines'), ' ') のタブ・括弧・山括弧・引用符をそれぞれ replace で空白に置換し、split(整形した文字列, ' ') をフィルターする。タブには decodeUriComponent('%09')、引用符には decodeUriComponent('%22') と decodeUriComponent('%27') を使える。

~~~
or(startsWith(item(), 'https://'), startsWith(item(), 'http://'))
~~~

各 URL の末尾にある . , ; は endsWith と slice(URL, 0, -1) で繰り返し取り除く。得られた配列を本文順に保持する。汎用 URL は配列の最初の要素。配列が空なら URL は空とする。

IsPipeline または RefMark に応じて Needle を /-/pipelines/、/-/merge_requests/、/-/issues/ のいずれかに設定する。対象種別は Kind が unknown でも保持する。URL 配列を逐次処理し、contains(item(), variables('Needle')) が真の場合だけ、次の式で直後の文字列を取得する。

~~~
last(split(item(), variables('Needle')))
~~~

chunk(直後の文字列, 1) を先頭から逐次処理し、最初の数字以外の文字が現れるまで数字を連結する。数字が 1 桁以上ある最初の URL を採用し、CLI と同様に数字列までを URL とする。Compose「IdDigits」に連結結果を置く場合の式は以下。パイプラインの number は isInt(outputs('IdDigits')) が真の場合に int(outputs('IdDigits')) で取得する。条件に合う URL がない場合や Needle を設定しなかった場合は汎用 URL を使う。

~~~
slice(item(), 0, add(add(lastIndexOf(item(), variables('Needle')), length(variables('Needle'))), length(outputs('IdDigits'))))
~~~

project は、本文順で最初に /-/ を含むプロジェクト URL から復元する。フィルター結果が空の場合はセグメントが 2 個以上なら first(body('Segments')) を使う。候補がある場合は Compose「ProjectPrefix」に first(split(URL, '/-/')) を入れ、次の式でスキームとホストを除く。結果例は platform/order-service。複数プロジェクトへのリンクを含む本文では、この URL 由来の値が CLI のヘッダー優先の値と異なることがある。

~~~
join(skip(split(last(split(outputs('ProjectPrefix'), '://')), '/'), 1), '/')
~~~

## ブランチと操作した人

MR（RefMark が ! で、パイプライン以外）は Lines を次の条件でフィルターし、最初の該当行を Compose「BranchLine」に入れる。前後の空白を除いて最初の単語を取ると、Branches: X to Y の X になる。該当行がなければ branch は空。パイプラインの branch は件名から取得した値。

~~~
or(startsWith(toLower(trim(item())), 'branches:'), startsWith(toLower(trim(item())), 'branch:'))
~~~

~~~
first(split(trim(slice(outputs('BranchLine'), add(indexOf(outputs('BranchLine'), ':'), 1))), ' '))
~~~

FromDisplay を trim した Compose「DisplayName」から、末尾の (@user) を除く。結果を Compose「FromActor」に入れ、空文字または GitLab（大小無視）の場合は actor を空にする。

~~~
if(and(
    greaterOrEquals(lastIndexOf(outputs('DisplayName'), '(@'), 0),
    endsWith(outputs('DisplayName'), ')')
  ),
  trim(slice(outputs('DisplayName'), 0, lastIndexOf(outputs('DisplayName'), '(@'))),
  outputs('DisplayName'))
~~~

パイプラインでは、Lines から triggered by を含む最初の行を Compose「TriggerLine」に入れ、その行の定型句より後を Compose「AfterTriggered」に入れる。

~~~
trim(slice(outputs('TriggerLine'), add(indexOf(outputs('TriggerLine'), 'triggered by '), length('triggered by '))))
~~~

AfterTriggered に括弧付きリンクがあれば、リンクより前を actor にする。標準的なリンク表記（「( https://… )」と「(https://…)」）に対応する例。

~~~
trim(first(split(first(split(replace(outputs('AfterTriggered'), '( ', '('), '(https://')), '(http://')))
~~~
空の場合は後述のラベル行と同様に Commit Author: の値を探し、それも空なら FromActor を使う。

## 要約（CLI アルゴリズムの近似）

HTML 変換、折り返し、定型句の部分一致の差により、要約は CLI の近似になる。

BodyCore を順に処理して、空行を除く。本文行をまだ採用していない間は、次の行も除く。

- 通知の定型行を最初の 1 行だけ除く。created an issue、requested a new review、commented などの定型句を toLower と contains で検査する。
- Assignee / Assignees、Reviewer / Reviewers、Author / Authors、Branch / Branches、Label / Labels、Milestone、Due date、Approver / Approvers、Project、Merge request、Issue にコロンが続くメタ行と、URL だけの行を除く。

採用した行は trim(item()) で配列に追加し、join(配列, ' ') で要約にする。

パイプラインでは Lines から Commit Message: の最初の行を抽出し、コロンの後ろを trim する。括弧付きリンクが行末にあれば除く。同じ行抽出を Commit Author: にも使える。Lines を逐次処理し、Stage: の値を保持して、次の Name: の値を stage/name として配列に追加する。ステージが空ならジョブ名だけを追加し、追加のたびにステージを空にする。ジョブがあれば次の式で要約の 1 要素を作る。

~~~
concat('失敗したジョブ: ', join(variables('FailedJobs'), ', '))
~~~

コミットメッセージとジョブの要素は、値のあるものだけを配列に追加して join(配列, ' / ') する。両方とも空なら通常の要約を使う。どちらの経路も、Compose「SummaryText」に入れた結果を 280 文字以内に整える。

~~~
if(empty(outputs('SummaryText')), null,
  if(greater(length(outputs('SummaryText')), 280),
    concat(trim(slice(outputs('SummaryText'), 0, 279)), '…'),
    outputs('SummaryText')))
~~~

## カードへの受け渡し

Issue 作成・MR レビュー依頼のカードには title の末尾に (#n) / (!n) を付ける。number は整数として保持し、カードに書くときに string() で変換する。残りのカード項目は [フロー構成](flow.md) と [CLI の出力例](expected/) を参照。受信日時は Outlook の receivedDateTime を使う。CLI はメールの Date を使うため、タイムゾーン表記や時刻が異なる場合がある。
