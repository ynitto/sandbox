---
name: api-designer
description: REST / GraphQL API の設計・ドキュメント生成・バリデーション方針を支援する。「APIを設計して」「REST APIのエンドポイントを決めて」「OpenAPIスキーマを作って」「GraphQLスキーマを設計して」「APIのバージョニング戦略を決めて」などのリクエストで発動する。Claude Code / GitHub Copilot 両環境で動作する。フロントエンドとバックエンドの両輪を揃え、フルスタック開発のカバレッジを向上させる。
metadata:
  version: "1.0"
---

# API Designer

REST / GraphQL API の設計から OpenAPI / GraphQL スキーマ生成まで一貫して支援するスキル。

## 前後の工程

- **前工程**: ユースケース・エンティティ洗い出しが完了した状態で本スキルを開始する
- **後工程（フロントエンド）**: 生成した OpenAPI スキーマをフロントエンド I/F 設計のインプットとして渡す
- **後工程（DB）**: エンドポイント設計と DB アクセスパターンを整合させる

---

## API 設計フロー

## 実行ルール（必須）

- 各 Step は **入力確認 → 実施 → 出力** の順で進める
- 未入力項目がある場合は、Step 1 の質問テンプレートで補完してから次に進む
- API 方式の採用判断は、Step 2 の判定マトリクスと「採用しない理由」テンプレートを必ず残す
- 互換性ポリシー（breaking / non-breaking / deprecation）は Step 3 と Step 6 の両方で確認する
- Step 6 のセキュリティ最小チェックは省略しない

### Step 1: ユースケースと必須入力チェック

#### 入力確認（必須チェックリスト）

以下を **必須入力項目** として確認する。未入力がある場合は質問して埋める。

- 主要なエンティティ（User, Order, Product など）
- 主要なユースケース（CRUD / 検索 / 集計 / バッチ / Webhook など）
- クライアント種別（SPA / モバイル / サードパーティ / 社内）
- 想定トラフィック・ピーク特性（RPS、同時接続、バースト）
- SLA / SLO（可用性、レスポンス目標、エラー予算）
- 可用性要件（冗長化、フェイルオーバー、RTO/RPO）
- 監査要件（誰が・いつ・何を変更したかの追跡要否）
- 非機能要件（性能、拡張性、運用性、可観測性、コスト）
- 法令 / 規制要件（業法、データ越境、保持期間）
- PII / 機微情報の分類（氏名、メール、住所、決済情報など）
- 認証 / 認可方式の制約（OAuth2, JWT, SSO, RBAC/ABAC）

#### 未入力時の質問テンプレート（必須）

不足項目を次のテンプレートで質問する。

```markdown
不足している前提を確認します。以下を教えてください（未定は「未定」で可）。
1. SLA/SLO（例: 可用性 99.9%、p95 300ms）
2. 可用性要件（RTO/RPO、障害時の許容停止時間）
3. 監査要件（監査ログ必須項目、保持期間、検索要件）
4. 非機能要件（性能/スケール/運用/コスト制約）
5. 法令・規制要件（個人情報、業法、保存場所制約）
6. PII の有無と項目（例: メール、電話、住所、決済情報）
```

#### Step 1 の出力

- `前提条件サマリー`
- `未確定事項リスト`

### Step 2: API 種別の選択（REST vs GraphQL）

| 観点 | REST | GraphQL | 判定時の確認ポイント |
|------|------|---------|----------------------|
| 学習コスト | 低 | 中〜高 | チームの経験値と教育コスト |
| 柔軟なデータ取得 | 難 | 容易 | 画面ごとの取得差分が大きいか |
| キャッシュ | HTTP キャッシュ標準 | 追加実装が必要 | CDN / ブラウザキャッシュ要件 |
| N+1 問題 | 設計で回避 | DataLoader 必須 | リレーションの深さ |
| 型安全性 | OpenAPI で担保 | スキーマで担保 | コード生成運用の有無 |
| 監査・統制 | 実装しやすい | 設計次第で複雑化 | 監査要件の厳しさ |
| 外部公開適性 | 高 | 要ガバナンス | サードパーティ公開予定 |
| 採用場面 | シンプルな CRUD・広い互換性 | 複雑なリレーション・フロントエンド主導 | 実際のユースケース適合 |

#### 「採用しない理由」記録テンプレート（必須）

```markdown
## API 方式の採用判断
- 採用方式: REST | GraphQL
- 採用理由:
  -
  -
- 非採用方式: REST | GraphQL
- 採用しない理由:
  -
  -
- トレードオフ:
  -
- 将来再評価の条件:
  -
```

#### 判定サマリーテンプレート（必須）

```markdown
## Step 2 判定サマリー
- 判定日:
- 判定者:
- 前提ユースケース:
- 判定結果: REST | GraphQL
- 主な決め手（3点以内）:
  1.
  2.
  3.
```

#### Step 2 の出力

- `API方式判定結果`
- `採用しない理由メモ`

### Step 3: エンドポイント / スキーマ境界設計

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

#### 互換性ポリシー（必須）

- **non-breaking**（同一メジャー内で許可）
  - 省略可能フィールドの追加
  - 新規エンドポイント / Query の追加
  - Enum 値の追加（クライアント許容方針がある場合）
- **breaking**（メジャー更新または明示移行が必須）
  - 必須フィールド追加
  - フィールド削除 / 型変更
  - パス変更・意味変更・認可要件の厳格化
- **deprecation 手順（必須）**
  1. 非推奨告知（変更理由・移行先・期限）
  2. 併存期間の明示（例: 90日）
  3. SDK / ドキュメント / サンプル更新
  4. 利用状況モニタリング（アクセス比率）
  5. 廃止実施と変更履歴記録

#### Step 3 の出力

- `API境界定義（resource / query / mutation）`
- `互換性方針メモ（breaking/non-breaking/deprecation）`

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

#### Step 4 の出力

- `リクエスト/レスポンス仕様`
- `エラー仕様・ステータスコード対応表`

### Step 5: OpenAPI / GraphQL スキーマ生成

**REST の場合** → [references/openapi-guide.md](references/openapi-guide.md) を読み込み、OpenAPI 3.0 YAML を生成する。

**GraphQL の場合** → 以下の GraphQL 設計ガイドに従いスキーマを生成する。

#### Step 5 の出力

- `OpenAPI 3.0 YAML` または `GraphQL SDL`
- `生成時の前提・制約メモ`

### Step 6: レビューと調整（互換性 + セキュリティ必須）

#### 互換性レビュー（必須）

- breaking 変更の有無を判定する
- breaking なら、メジャーバージョン更新または deprecation 手順を適用する
- non-breaking でもクライアント影響（SDK生成、型定義）を確認する

#### セキュリティ最小チェック（必須）

以下 5 項目は全てチェックする。

1. **認可境界**: 誰がどのリソース/フィールドを操作できるか明確か
2. **PII 保護**: 収集最小化、マスキング、出力制御、保存方針が定義されているか
3. **監査ログ**: 認証/認可失敗・更新系操作を追跡可能か
4. **レート制御**: 乱用対策（IP/トークン単位制限、429設計）があるか
5. **情報露出**: エラーメッセージやレスポンスに内部情報を含めていないか

#### 整合性レビュー

- エンドポイント・フィールドの過不足
- フロントエンド・DB との整合性

#### Step 6 の出力

- `レビュー結果（互換性 / セキュリティ / 整合性）`
- `修正アクション一覧`

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

### 出力フォーマット（必須）

最終出力は次の順で提示する。

1. `前提条件サマリー`
2. `API方式判定結果`（採用しない理由を含む）
3. `API仕様`（エンドポイントまたはスキーマ）
4. `互換性ポリシー`（breaking/non-breaking/deprecation）
5. `セキュリティ最小チェック結果`
6. `DoD 判定`
7. `次工程ハンドオフ`

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

### Definition of Done（DoD）チェックリスト（必須）

```markdown
- [ ] Step 1 の必須入力チェックリストが埋まっている
- [ ] REST/GraphQL 判定マトリクスと「採用しない理由」が記録されている
- [ ] 互換性ポリシー（breaking/non-breaking/deprecation）が明記されている
- [ ] セキュリティ最小チェック5項目が全て確認済み
- [ ] OpenAPI lint を通過している（REST を採用した場合のみ）
- [ ] GraphQL schema validation を通過している（GraphQL を採用した場合のみ）
- [ ] フロントエンド / DB へのハンドオフ項目が整理されている
```

### 次工程ハンドオフ項目（必須）

```markdown
## フロントエンド向け
- 利用エンドポイント / クエリ一覧:
- 認証方式・必要スコープ:
- エラーコードとUIハンドリング方針:
- 互換性注意点（deprecation期限含む）:

## バックエンド / DB向け
- 想定アクセスパターン（一覧・検索・集計）:
- インデックス/キャッシュ検討ポイント:
- 監査ログ保存要件:
- PII 取り扱いポリシー:
```

---

## リファレンス

詳細な OpenAPI 3.0 スニペット集: [references/openapi-guide.md](references/openapi-guide.md)
