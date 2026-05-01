# Redmine API リファレンス

Redmine REST API の主要エンドポイントとパラメータのリファレンス。

公式ドキュメント: https://www.redmine.org/projects/redmine/wiki/Rest_api

## 目次

- [認証](#認証)
- [チケット一覧 GET /issues.json](#チケット一覧-get-issuejson)
- [チケット詳細 GET /issues/{id}.json](#チケット詳細-get-issuesid-json)
- [チケット更新 PUT /issues/{id}.json](#チケット更新-put-issuesid-json)
- [コメント投稿](#コメント投稿)
- [共通ステータスID](#共通ステータスidデフォルト)
- [共通優先度ID](#共通優先度idデフォルト)
- [エラーレスポンス](#エラーレスポンス)

---

## 認証

すべてのリクエストに以下のいずれかを付与する:

| 方法 | 形式 |
|------|------|
| リクエストヘッダー（推奨） | `X-Redmine-API-Key: <api_key>` |
| クエリパラメータ | `?key=<api_key>` |

APIキーは Redmine の「個人設定」ページで確認・再生成できる。

---

## チケット一覧 GET /issues.json

### クエリパラメータ

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `project_id` | string/int | プロジェクトIDまたはスラッグ |
| `status_id` | string | `open` / `closed` / `*` / ステータスID |
| `tracker_id` | int | トラッカーID |
| `priority_id` | int | 優先度ID |
| `author_id` | int/string | 作成者ID（`me` で自分） |
| `assigned_to_id` | int/string | 担当者ID（`me` で自分） |
| `subject` | string | 件名フィルタ（部分一致） |
| `created_on` | string | 作成日時フィルタ（演算子付き） |
| `updated_on` | string | 更新日時フィルタ（演算子付き） |
| `due_date` | string | 期日フィルタ（演算子付き） |
| `sort` | string | ソート順（`フィールド:asc/desc`） |
| `limit` | int | 取得件数（デフォルト: 25、最大: 100） |
| `offset` | int | 取得オフセット |
| `include` | string | 追加情報（`journals,attachments,watchers` をカンマ区切り） |

### 日時フィルタの演算子

| 演算子 | 意味 | 例 |
|--------|------|-----|
| `>=YYYY-MM-DD` | 以降 | `>=2025-01-01` |
| `<=YYYY-MM-DD` | 以前 | `<=2025-03-31` |
| `><FROM\|TO` | 範囲内 | `><2025-01-01\|2025-03-31` |
| `=YYYY-MM-DD` | 特定日 | `=2025-01-15` |

### レスポンス

```json
{
  "issues": [
    {
      "id": 1234,
      "project": {"id": 1, "name": "My Project"},
      "tracker": {"id": 1, "name": "バグ"},
      "status": {"id": 1, "name": "新規"},
      "priority": {"id": 2, "name": "通常"},
      "author": {"id": 1, "name": "管理者"},
      "assigned_to": {"id": 5, "name": "山田太郎"},
      "subject": "ログイン画面でエラーが発生する",
      "description": "...",
      "done_ratio": 0,
      "created_on": "2025-01-10T09:00:00Z",
      "updated_on": "2025-01-15T14:30:00Z"
    }
  ],
  "total_count": 42,
  "offset": 0,
  "limit": 25
}
```

---

## チケット詳細 GET /issues/{id}.json

### クエリパラメータ

| パラメータ | 説明 |
|-----------|------|
| `include` | 追加情報（`journals`, `attachments`, `watchers`, `changesets`, `relations` など） |

### レスポンス

```json
{
  "issue": {
    "id": 1234,
    "subject": "...",
    "description": "...",
    "status": {"id": 1, "name": "新規"},
    "priority": {"id": 2, "name": "通常"},
    "tracker": {"id": 1, "name": "バグ"},
    "project": {"id": 1, "name": "My Project"},
    "author": {"id": 1, "name": "管理者"},
    "assigned_to": {"id": 5, "name": "山田太郎"},
    "done_ratio": 0,
    "due_date": null,
    "created_on": "2025-01-10T09:00:00Z",
    "updated_on": "2025-01-15T14:30:00Z",
    "journals": [
      {
        "id": 100,
        "user": {"id": 5, "name": "山田太郎"},
        "notes": "調査しました。",
        "created_on": "2025-01-11T10:00:00Z",
        "details": [
          {
            "property": "attr",
            "name": "status_id",
            "old_value": "1",
            "new_value": "2"
          }
        ]
      }
    ]
  }
}
```

---

## チケット更新 PUT /issues/{id}.json

### リクエストボディ

```json
{
  "issue": {
    "subject": "新しい件名",
    "description": "新しい説明",
    "status_id": 2,
    "priority_id": 3,
    "tracker_id": 1,
    "assigned_to_id": 5,
    "done_ratio": 50,
    "due_date": "2025-03-31",
    "notes": "コメント本文（省略可）"
  }
}
```

成功時のステータスコード: `200 OK`（レスポンスボディなし）

---

## コメント投稿 PUT /issues/{id}.json（notes フィールドを使用）

コメントの投稿はチケット更新と同じエンドポイントを使用する。  
`notes` フィールドにコメント本文を入れて PUT する。

```json
{
  "issue": {
    "notes": "コメント本文"
  }
}
```

成功時のステータスコード: `200 OK`

---

## 共通ステータスID（デフォルト）

Redmine のデフォルト設定:

| ID | 名前 |
|----|------|
| 1 | 新規 |
| 2 | 進行中 |
| 3 | 解決 |
| 4 | フィードバック |
| 5 | 終了 |
| 6 | 却下 |

※ Redmine の設定によって異なる。実際のIDは Redmine 管理画面で確認すること。

## 共通優先度ID（デフォルト）

| ID | 名前 |
|----|------|
| 1 | 低め |
| 2 | 通常 |
| 3 | 高め |
| 4 | 急いで |
| 5 | 今すぐ |

---

## エラーレスポンス

```json
{
  "errors": ["ステータスは不正な値です"]
}
```

| HTTP ステータス | 意味 |
|----------------|------|
| `200 OK` | 成功 |
| `201 Created` | 作成成功 |
| `401 Unauthorized` | APIキーが不正または未設定 |
| `403 Forbidden` | アクセス権なし |
| `404 Not Found` | リソースが存在しない |
| `422 Unprocessable Entity` | バリデーションエラー |
| `500 Internal Server Error` | サーバーエラー |
