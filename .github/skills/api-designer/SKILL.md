---
name: api-designer
description: REST / GraphQL API の設計・ドキュメント生成・バリデーション方針を支援する。「APIを設計して」「REST APIのエンドポイントを決めて」「OpenAPIスキーマを作って」「GraphQLスキーマを設計して」「APIのバージョニング戦略を決めて」などのリクエストで発動する。Claude Code / GitHub Copilot 両環境で動作する。フロントエンドとバックエンドの両輪を揃え、フルスタック開発のカバレッジを向上させる。
---

# API Designer

REST / GraphQL API の設計から OpenAPI / GraphQL スキーマ生成まで一貫して支援するスキル。

## スキルエコシステムにおける位置づけ

```
requirements-definer  →  api-designer  →  react-frontend-coder
                              ↓
                       dynamodb-designer
```

- **requirements-definer**: ユースケース・エンティティ洗い出しが完了した状態で引き継ぐ
- **react-frontend-coder**: 生成した OpenAPI スキーマをフロントエンド I/F 設計のインプットとして渡す
- **dynamodb-designer**: エンドポイント設計と DB アクセスパターンを整合させる

---

## API 設計フロー

### Step 1: ユースケースとリソースの確認

ユーザーに以下を確認する（未定義の場合）:

- 主要なエンティティ（User, Order, Product など）
- 主要なユースケース（CRUD / 検索 / 集計など）
- クライアント種別（SPA / モバイル / サードパーティ）
- 想定トラフィック規模・チームのスキルセット

### Step 2: API 種別の選択（REST vs GraphQL）

| 観点 | REST | GraphQL |
|------|------|---------|
| 学習コスト | 低 | 中〜高 |
| 柔軟なデータ取得 | 難 | 容易 |
| キャッシュ | HTTP キャッシュ標準 | 追加実装が必要 |
| N+1 問題 | 設計で回避 | DataLoader 必須 |
| 型安全性 | OpenAPI で担保 | スキーマで担保 |
| 採用場面 | シンプルな CRUD・広い互換性 | 複雑なリレーション・フロントエンド主導 |

### Step 3: エンドポイント設計

#### REST 命名規約

```
# リソースは名詞・複数形
GET    /users            # 一覧
POST   /users            # 作成
GET    /users/{id}       # 取得
PUT    /users/{id}       # 全体更新
PATCH  /users/{id}       # 部分更新
DELETE /users/{id}       # 削除

# ネストは 2 階層まで
GET /users/{userId}/orders
GET /users/{userId}/orders/{orderId}

# アクションは動詞サブリソースで表現
POST /orders/{id}/cancel
POST /users/{id}/password-reset
```

#### バージョニング戦略

| 方式 | 例 | 特徴 |
|------|-----|------|
| URL パス | `/v1/users` | 可視性高・ルーティング容易 |
| ヘッダー | `Accept: application/vnd.api+json;v=1` | URL をクリーンに保てる |
| クエリパラメータ | `/users?version=1` | テスト容易だがキャッシュしにくい |

推奨: **URL パス方式**（可視性・互換性が最も高い）

### Step 4: リクエスト / レスポンス設計

#### 標準レスポンス構造

```json
// 成功（単一リソース）
{
  "data": { "id": "123", "name": "Alice" },
  "meta": { "requestId": "abc-xyz" }
}

// 成功（コレクション）
{
  "data": [...],
  "pagination": {
    "cursor": "eyJpZCI6MTAwfQ==",
    "hasNext": true,
    "total": 500
  }
}
```

#### エラーレスポンス（RFC 7807 Problem Details）

```json
{
  "type": "https://api.example.com/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "The request body contains invalid fields.",
  "instance": "/users/register",
  "errors": [
    { "field": "email", "message": "Invalid email format" }
  ]
}
```

#### HTTP ステータスコード

| コード | 用途 |
|--------|------|
| 200 | 成功（GET / PUT / PATCH） |
| 201 | 作成成功（POST） |
| 204 | 削除成功（DELETE） |
| 400 | リクエスト不正 |
| 401 | 未認証 |
| 403 | 権限不足 |
| 404 | リソース未発見 |
| 409 | 競合（重複など） |
| 422 | バリデーションエラー |
| 429 | レートリミット超過 |
| 500 | サーバー内部エラー |

#### ページネーション設計

- **カーソルベース**: タイムライン・無限スクロール向け（件数が多い・更新頻度が高いデータ）
- **オフセットベース**: 管理画面・ページ指定が必要な UI 向け

```
# カーソルベース
GET /posts?limit=20&after=eyJpZCI6MTAwfQ==

# オフセットベース
GET /products?page=3&perPage=25
```

#### 認証・認可設計

| 方式 | 採用場面 |
|------|----------|
| JWT（Bearer Token） | SPA / モバイルアプリ |
| OAuth2（Authorization Code） | サードパーティ連携 |
| API Key | サーバー間通信・開発者向け API |
| Session Cookie | SSR・従来型 Web アプリ |

### Step 5: OpenAPI / GraphQL スキーマ生成

**REST の場合** → [references/openapi-guide.md](references/openapi-guide.md) を読み込み、OpenAPI 3.0 YAML を生成する。

**GraphQL の場合** → 以下の GraphQL 設計ガイドに従いスキーマを生成する。

### Step 6: レビューと調整

生成したスキーマをユーザーに提示し、以下を確認する:

- エンドポイント・フィールドの過不足
- セキュリティ要件（認証方式・スコープ）
- 破壊的変更の有無（既存 API の更新時）
- フロントエンド・DB との整合性

---

## REST API ベストプラクティス

### バリデーション設計

- 入力バリデーションはコントローラ層で実施（スキーマバリデーターを利用）
- 必須フィールド・型・フォーマット・範囲を OpenAPI スキーマで宣言
- エラーは `errors` 配列で複数フィールドをまとめて返す

### レートリミット

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1700000000
```

### 冪等性

- GET / PUT / DELETE は冪等に設計する
- POST で冪等性が必要な場合は `Idempotency-Key` ヘッダーを使う

---

## GraphQL 設計ガイド

### スキーマ設計（Query / Mutation / Subscription）

```graphql
type Query {
  user(id: ID!): User
  users(filter: UserFilter, pagination: PaginationInput): UserConnection!
}

type Mutation {
  createUser(input: CreateUserInput!): CreateUserPayload!
  updateUser(id: ID!, input: UpdateUserInput!): UpdateUserPayload!
  deleteUser(id: ID!): DeleteUserPayload!
}

type Subscription {
  orderStatusChanged(orderId: ID!): Order!
}

# Relay スタイルの Cursor Connection
type UserConnection {
  edges: [UserEdge!]!
  pageInfo: PageInfo!
  totalCount: Int!
}
```

### N+1 問題対策（DataLoader パターン）

- リゾルバで直接 DB アクセスしない
- `DataLoader` でバッチ・キャッシュを実装する
- `@dataLoader` ディレクティブ等でリゾルバに宣言的に紐づける

### 認可設計（フィールドレベル）

```graphql
type User {
  id: ID!
  name: String!
  email: String! @auth(requires: OWNER_OR_ADMIN)
  internalNotes: String @auth(requires: ADMIN)
}
```

---

## 成果物テンプレート

### エンドポイント一覧表（Markdown）

```markdown
| メソッド | パス | 説明 | 認証 |
|--------|------|------|------|
| GET | /v1/users | ユーザー一覧取得 | JWT |
| POST | /v1/users | ユーザー作成 | JWT |
| GET | /v1/users/{id} | ユーザー取得 | JWT |
| PUT | /v1/users/{id} | ユーザー更新 | JWT（本人/管理者） |
| DELETE | /v1/users/{id} | ユーザー削除 | JWT（管理者） |
```

### エラーコード定義表

```markdown
| コード | HTTP ステータス | 説明 |
|--------|----------------|------|
| USER_NOT_FOUND | 404 | 指定したユーザーが存在しない |
| EMAIL_ALREADY_EXISTS | 409 | メールアドレスが既に使用中 |
| INVALID_TOKEN | 401 | JWT が無効または期限切れ |
| INSUFFICIENT_PERMISSION | 403 | 操作に必要な権限がない |
| VALIDATION_ERROR | 422 | リクエストのバリデーション失敗 |
| RATE_LIMIT_EXCEEDED | 429 | レートリミット超過 |
```

---

## リファレンス

詳細な OpenAPI 3.0 スニペット集: [references/openapi-guide.md](references/openapi-guide.md)
