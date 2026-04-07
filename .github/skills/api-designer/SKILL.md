---
name: api-designer
description: REST API の設計・OpenAPI ドキュメント生成・バリデーション方針を支援する。「APIを設計して」「REST APIのエンドポイントを決めて」「OpenAPIスキーマを作って」「APIのバージョニング戦略を決めて」などのリクエストで必ずこのスキルを使う。
metadata:
  version: 2.0.0
  tier: stable
  category: design
  tags:
    - api
    - openapi
    - rest
---

# API Designer

REST API の設計から OpenAPI スキーマ生成まで一貫して支援するスキル。
ドメインモデル・既存 OpenAPI 仕様・実装コードを入力として受け取り、それぞれの制約に応じた最適なインターフェースを決定する。

## 前後の工程

- **前工程**: ドメインモデル設計、既存 API 仕様・実装コードの収集
- **後工程（フロントエンド）**: 生成した OpenAPI スキーマをフロントエンド I/F 設計のインプットとして渡す
- **後工程（DB）**: エンドポイント設計と DB アクセスパターンを整合させる

---

## 実行ルール（必須）

- 各 Step は **入力確認 → 実施 → 出力** の順で進める
- Step 1 の入力収集はユーザーと対話しながら行う。存在を確認し、あれば受け取る
- Step 2 の制約分析は入力の種類によって判断基準が異なる。必ずテンプレートに従い明示する
- 互換性ポリシー（breaking / non-breaking / deprecation）は Step 3 と Step 6 の両方で確認する
- Step 6 のセキュリティ最小チェックと OpenAPI ドキュメント品質チェックは省略しない

---

## API 設計フロー

### Step 1: 入力収集とインプット確認

以下の入力を**ユーザーと対話しながら**確認・収集する。
複数の入力を受け付けてよい。存在しない場合は「なし」として進める。

#### 確認する入力（優先順位順）

1. **openapi_spec** — 既存の OpenAPI 仕様ファイル（YAML/JSON）
   - 「既存の OpenAPI 仕様はありますか？あればファイルまたは内容を共有してください。」
2. **implementation_code** — 既存の実装コード（ルーティング定義、コントローラ等）
   - 「既存の実装コードはありますか？ルーティング定義やコントローラのコードがあれば共有してください。」
3. **domain_model** — ドメインモデル（Mermaid classDiagram 等）
   - 「ドメインモデルはありますか？Mermaid classDiagram や ER 図があれば共有してください。」
4. **api_description** — 上記がない場合のフォールバック
   - 上記いずれもない場合: 「API の目的・概要を自由に教えてください。」

#### 追加で確認する情報

入力が揃ったら、不足していれば以下も確認する（「未定」で可）:

```
以下を確認します（未定は「未定」で可）。
1. API の利用者（SPA / モバイル / サードパーティ / 社内システム）
2. 認証・認可方式（JWT / OAuth2 / API Key / Session など）
3. バージョニング方針（新規 / 既存バージョン継続 / メジャー更新）
4. PII・機微情報の有無（氏名、メール、決済情報など）
5. レートリミット・SLA 要件（あれば）
```

#### Step 1 の出力

- `収集した入力サマリー`（種類・内容・有無）
- `追加確認事項サマリー`

---

### Step 2: 入力分析と制約判断

収集した入力を分析し、インターフェース設計の**制約レベル**を決定する。

#### 入力種別ごとの制約ルール

| 入力種別 | 制約レベル | 判断方針 |
|----------|-----------|---------|
| openapi_spec | **強制（原則変更不可）** | 定義済みパス・メソッド・スキーマは維持する。追加は可。削除・変更は breaking change として扱い、ユーザーの明示的な承認を得る |
| implementation_code | **コスト考慮（要相談）** | 実装との乖離コストを見積もり、変更量が大きい場合はユーザーに確認してから変更提案する |
| domain_model | **自由設計** | RESTful インターフェースへ具体化する。命名・構造はドメイン用語に従う |
| api_description のみ | **自由設計** | ベストプラクティスに従い新規設計する |

#### 複合入力時の優先順位

複数の入力がある場合は以下の順で制約を優先する:

```
openapi_spec（最優先）> implementation_code > domain_model > api_description
```

#### 制約分析テンプレート（必須）

```markdown
## 制約分析サマリー
- openapi_spec: あり / なし → [あり: 既存パス一覧と変更可否の判断]
- implementation_code: あり / なし → [あり: 変更コスト見積もり]
- domain_model: あり / なし → [あり: RESTful 具体化の方針]

## 変更制約マップ
| 対象 | 制約レベル | 変更可否 | 備考 |
|------|-----------|---------|------|
| （パス / スキーマ / 操作ごとに記載）|  |  |  |

## 設計方針サマリー
- 固定するインターフェース:
- 新規追加するインターフェース:
- 変更提案（ユーザー承認が必要なもの）:
```

#### Step 2 の出力

- `制約分析サマリー`
- `設計方針サマリー`（固定・追加・要確認の分類）

---

### Step 3: エンドポイント設計

Step 2 の制約に従いエンドポイントを設計する。

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
| URL パス | `/v1/users` | 可視性高・ルーティング容易（推奨） |
| ヘッダー | `Accept: application/vnd.api+json;v=1` | URL をクリーンに保てる |
| クエリパラメータ | `/users?version=1` | テスト容易だがキャッシュしにくい |

推奨: **URL パス方式**

#### 互換性ポリシー（必須）

- **non-breaking**（同一メジャー内で許可）
  - 省略可能フィールドの追加
  - 新規エンドポイントの追加
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

- `エンドポイント一覧（resource・メソッド・説明・認証）`
- `互換性方針メモ（breaking/non-breaking/deprecation）`

---

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
| 409 | 競合（重複・楽観ロック失敗など） |
| 422 | バリデーションエラー |
| 429 | レートリミット超過 |
| 500 | サーバー内部エラー |
| 503 | サービス一時停止（メンテナンス等） |

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
- `エラーコード定義表`（コード・HTTPステータス・発生条件・説明）

---

### Step 5: OpenAPI スキーマ生成

[references/openapi-guide.md](references/openapi-guide.md) を読み込み、OpenAPI 3.0 YAML を生成する。

#### 生成品質要件（必須）

生成する OpenAPI ファイルは**仕様書として単体で使える品質**にする。以下を必ず含める。

##### 1. 解説（description）

```yaml
# すべての operation に description を記載する
paths:
  /users/{id}:
    get:
      summary: ユーザー取得
      description: |
        指定された ID のユーザーを取得します。
        - 本人または管理者ロールのみアクセス可能です。
        - 削除済みユーザーは 404 を返します。

# すべての schema フィールドに description を記載する
components:
  schemas:
    User:
      properties:
        id:
          type: string
          description: ユーザーの一意識別子（UUID v4）
        email:
          type: string
          description: ログインに使用するメールアドレス。変更時は確認メールを送信する。
```

##### 2. スキーマ例（example / examples）

```yaml
# スキーマレベルの example（単体レスポンス用）
components:
  schemas:
    User:
      example:
        id: "550e8400-e29b-41d4-a716-446655440000"
        email: "alice@example.com"
        name: "Alice Smith"
        role: "user"
        createdAt: "2024-01-15T09:30:00Z"

# operationレベルの examples（複数シナリオ）
responses:
  '200':
    content:
      application/json:
        examples:
          regular_user:
            summary: 一般ユーザーの例
            value:
              data:
                id: "550e8400-e29b-41d4-a716-446655440000"
                email: "alice@example.com"
                role: "user"
          admin_user:
            summary: 管理者ユーザーの例
            value:
              data:
                id: "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
                email: "admin@example.com"
                role: "admin"
```

##### 3. エラーバリエーション（responses の網羅）

各 operation に対して発生しうるエラーレスポンスを**全パターン**列挙する。

```yaml
paths:
  /users/{id}:
    get:
      responses:
        '200':
          description: 取得成功
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          description: ユーザーが存在しない
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ProblemDetails'
              examples:
                user_not_found:
                  summary: ユーザーが見つからない場合
                  value:
                    type: "https://api.example.com/errors/not-found"
                    title: "Not Found"
                    status: 404
                    detail: "User with id '550e8400' does not exist."
                    instance: "/users/550e8400"
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'

# components に共通エラーレスポンスを定義
components:
  responses:
    Unauthorized:
      description: 認証トークンが無効または未提供
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            missing_token:
              summary: トークン未提供
              value:
                type: "https://api.example.com/errors/unauthorized"
                title: "Unauthorized"
                status: 401
                detail: "Authorization header is missing."
            expired_token:
              summary: トークン期限切れ
              value:
                type: "https://api.example.com/errors/unauthorized"
                title: "Unauthorized"
                status: 401
                detail: "Token has expired. Please re-authenticate."
    Forbidden:
      description: 認証済みだが権限不足
    TooManyRequests:
      description: レートリミット超過
      headers:
        Retry-After:
          schema:
            type: integer
          description: 次のリクエストが可能になるまでの秒数
    InternalServerError:
      description: サーバー内部エラー
```

#### Step 5 の出力

- `OpenAPI 3.0 YAML`（descriptions・examples・error responses を全て含む）
- `生成時の前提・制約メモ`

---

### Step 6: レビューと調整（互換性 + セキュリティ + ドキュメント品質）

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

#### OpenAPI ドキュメント品質チェック（必須）

| チェック項目 | 基準 |
|-------------|------|
| operation description | 全 operation に記載されているか |
| parameter description | 全パスパラメータ・クエリパラメータに記載されているか |
| schema フィールド description | 主要フィールドに記載されているか |
| example | 全 schema に example が定義されているか |
| エラーレスポンス網羅 | 各 operation の発生しうる 4xx/5xx が列挙されているか |
| エラー examples | 401/403/404/422/429 にシナリオ別 example があるか |
| components 共通化 | 繰り返しスキーマが $ref で共通化されているか |

#### 整合性レビュー

- エンドポイント・フィールドの過不足
- フロントエンド・DB との整合性

#### Step 6 の出力

- `レビュー結果（互換性 / セキュリティ / ドキュメント品質）`
- `修正アクション一覧`

---

## 成果物テンプレート

### 出力フォーマット（必須）

最終出力は次の順で提示する。

1. `収集した入力サマリー`
2. `制約分析サマリー`（入力種別ごとの制約と設計方針）
3. `エンドポイント一覧`（メソッド・パス・説明・認証）
4. `互換性ポリシー`（breaking/non-breaking/deprecation）
5. `OpenAPI 3.0 YAML`（解説・スキーマ例・エラーバリエーション含む）
6. `セキュリティ最小チェック結果`
7. `DoD 判定`
8. `次工程ハンドオフ`

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
| コード | HTTP ステータス | 発生条件 | 説明 |
|--------|----------------|---------|------|
| USER_NOT_FOUND | 404 | 指定 ID のユーザーが存在しない | ユーザーが見つかりません |
| EMAIL_ALREADY_EXISTS | 409 | 同一メールアドレスが登録済み | メールアドレスが既に使用中 |
| INVALID_TOKEN | 401 | JWT が無効または期限切れ | 認証トークンが無効です |
| INSUFFICIENT_PERMISSION | 403 | 操作に必要なロールがない | 権限が不足しています |
| VALIDATION_ERROR | 422 | リクエストボディのバリデーション失敗 | 入力値が不正です |
| RATE_LIMIT_EXCEEDED | 429 | レートリミット超過 | リクエスト上限を超えました |
```

### Definition of Done（DoD）チェックリスト（必須）

```markdown
- [ ] 入力を収集し制約分析を完了した
- [ ] 制約マップに従いエンドポイントを設計した（openapi_spec の既存インターフェースは維持）
- [ ] 互換性ポリシー（breaking/non-breaking/deprecation）が明記されている
- [ ] 全 operation に description が記載されている
- [ ] 全 schema に example が定義されている
- [ ] 全 operation の 4xx/5xx エラーレスポンスが列挙されている
- [ ] エラーレスポンスにシナリオ別 example がある
- [ ] セキュリティ最小チェック5項目が全て確認済み
- [ ] OpenAPI lint を通過している（validate_openapi.py）
- [ ] フロントエンド / DB へのハンドオフ項目が整理されている
```

### 次工程ハンドオフ項目（必須）

```markdown
## フロントエンド向け
- 利用エンドポイント一覧:
- 認証方式・必要スコープ:
- エラーコードと UI ハンドリング方針:
- 互換性注意点（deprecation 期限含む）:

## バックエンド / DB 向け
- 想定アクセスパターン（一覧・検索・集計）:
- インデックス/キャッシュ検討ポイント:
- 監査ログ保存要件:
- PII 取り扱いポリシー:
```

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

## 補助スクリプト

### scripts/

- **validate_openapi.py** — OpenAPI 3.x YAML / JSON ファイルのスキーマバリデーション

```bash
# YAML ファイルを検証（PyYAML が必要）
python scripts/validate_openapi.py openapi.yaml

# JSON ファイルを検証（標準ライブラリのみで動作）
python scripts/validate_openapi.py openapi.json

# 警告も表示（descriptions 未設定、servers 未定義等）
python scripts/validate_openapi.py --strict openapi.yaml

# JSON 形式で出力（CI 連携用）
python scripts/validate_openapi.py --json openapi.yaml
```

**検証項目**: openapi/info/paths の必須フィールド、パスパラメータの整合性、セキュリティスキーム参照、HTTPステータスコード形式、servers URL形式

**終了コード**: 0 = 通過 / 1 = エラーあり / 2 = ファイル不在・パースエラー

---

## リファレンス

詳細な OpenAPI 3.0 スニペット集: [references/openapi-guide.md](references/openapi-guide.md)
