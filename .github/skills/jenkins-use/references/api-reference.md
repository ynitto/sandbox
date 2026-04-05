# Jenkins REST API リファレンス

## 目次

- [認証](#認証)
- [主要エンドポイント](#主要エンドポイント)
- [CSRF 保護（crumb）](#csrf-保護crumb)
- [tree パラメータ](#tree-パラメータ)
- [Folder / Multibranch Pipeline](#folder--multibranch-pipeline)
- [よく使う curl の例](#よく使う-curl-の例)

---

## 認証

Jenkins REST API は HTTP Basic 認証を使用する。パスワードの代わりに API トークンを使うこと。

```
Authorization: Basic base64(user:api-token)
```

API トークンの生成: Jenkins → ユーザー名 → 設定 → API Token → Add new Token

---

## 主要エンドポイント

### サーバー情報

```
GET /api/json
```

レスポンス（抜粋）:
```json
{
  "version": "2.440.1",
  "jobs": [...],
  "description": null
}
```

### ジョブ一覧

```
GET /api/json?tree=jobs[name,url,color]
```

`color` の値:
- `blue` → 最後のビルドが成功
- `red` → 最後のビルドが失敗
- `notbuilt` → 未実行
- `disabled` → 無効化済み
- `*_anime` → 現在実行中（例: `blue_anime`）

### ビルドのトリガー

パラメータなし:
```
POST /job/{job-name}/build
```

パラメータあり:
```
POST /job/{job-name}/buildWithParameters
Content-Type: application/x-www-form-urlencoded

PARAM1=value1&PARAM2=value2
```

レスポンス: `201 Created` + `Location: /queue/item/{id}/`

### キューアイテムの確認

```
GET /queue/item/{id}/api/json
```

レスポンス（抜粋）:
```json
{
  "id": 123,
  "why": "キューで待機中",
  "executable": {             // ビルドが開始されたら現れる
    "number": 42,
    "url": "http://jenkins/job/my-job/42/"
  },
  "cancelled": false
}
```

### ビルド情報

```
GET /job/{job-name}/{build-number}/api/json
GET /job/{job-name}/lastBuild/api/json
GET /job/{job-name}/lastSuccessfulBuild/api/json
GET /job/{job-name}/lastFailedBuild/api/json
```

レスポンス（抜粋）:
```json
{
  "number": 42,
  "result": "SUCCESS",        // SUCCESS / FAILURE / ABORTED / UNSTABLE / null(実行中)
  "building": false,
  "timestamp": 1705123456000, // Unix ミリ秒
  "duration": 222000,         // ミリ秒
  "estimatedDuration": 240000,
  "url": "http://jenkins/job/my-job/42/",
  "displayName": "#42",
  "description": null,
  "actions": [...]
}
```

### ビルド一覧

```
GET /job/{job-name}/api/json?tree=builds[number,result,timestamp,duration,url]{10}
```

`{10}` は取得件数（スライス記法）。

### コンソールログ

全文取得:
```
GET /job/{job-name}/{build-number}/consoleText
```

プログレッシブ取得（ストリーミング用）:
```
GET /job/{job-name}/{build-number}/logText/progressiveText?start={offset}
```

レスポンスヘッダー:
- `X-Text-Size`: 次回リクエストで使う `start` オフセット
- `X-More-Data: true`: まだログが続いている（ビルド実行中）

---

## CSRF 保護（crumb）

CSRF 保護が有効な Jenkins では POST リクエストに crumb ヘッダーが必要:

```
GET /crumbIssuer/api/json
```

レスポンス:
```json
{
  "crumbRequestField": "Jenkins-Crumb",
  "crumb": "abc123..."
}
```

取得した crumb を POST リクエストのヘッダーに付与:
```
Jenkins-Crumb: abc123...
```

CSRF が無効の場合は `/crumbIssuer/api/json` が `404` を返す。

---

## tree パラメータ

`tree` クエリパラメータでレスポンスフィールドを絞り込み、転送量を削減できる:

```
# ビルド番号と結果のみ
/job/my-job/api/json?tree=builds[number,result]

# ネストしたフィールドも指定可能
/job/my-job/api/json?tree=builds[number,result,actions[parameters[name,value]]]

# 件数をスライスで制限
/job/my-job/api/json?tree=builds[number,result]{0,5}
```

---

## Folder / Multibranch Pipeline

フォルダー内のジョブにアクセスする場合は階層を `job` で区切る:

```
# フォルダー配下のジョブ
/job/my-folder/job/my-job/api/json

# Multibranch Pipeline のブランチ
/job/my-pipeline/job/main/api/json
/job/my-pipeline/job/feature%2Fmy-branch/api/json  # スラッシュは %2F にエンコード
```

---

## よく使う curl の例

```bash
# 接続確認
curl -s -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/api/json?tree=version" | python3 -m json.tool

# ビルドをトリガー（パラメータあり）
curl -X POST -u "$JENKINS_USER:$JENKINS_TOKEN" \
  -H "$(curl -s -u "$JENKINS_USER:$JENKINS_TOKEN" \
    "$JENKINS_URL/crumbIssuer/api/json" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['crumbRequestField']+': '+d['crumb'])")" \
  "$JENKINS_URL/job/my-pipeline/buildWithParameters?BRANCH=main"

# 最新ビルドの状態を確認
curl -s -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/job/my-pipeline/lastBuild/api/json?tree=number,result,building" | \
  python3 -m json.tool
```
