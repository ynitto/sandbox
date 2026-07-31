# OpenAPI 3.0 スニペット集

SKILL.md の Step 5 で OpenAPI YAML を生成する際に参照する。
生成物は**仕様書として単体で使える品質**にする：解説（description）・スキーマ例（example）・エラーバリエーションを全て含めること。

## 目次

1. [ベース構造](#ベース構造)
2. [パス・操作定義（description・examples 付き）](#パス操作定義)
3. [スキーマ定義（description・example 付き）](#スキーマ定義)
4. [認証定義](#認証定義)
5. [ページネーション](#ページネーション)
6. [エラーレスポンス（バリエーション付き）](#エラーレスポンス)

---

## ベース構造

```yaml
openapi: 3.0.3
info:
  title: Example API
  version: 1.0.0
  description: |
    Example API の概要説明。

    ## 認証
    すべてのエンドポイントは Bearer トークン（JWT）による認証が必要です。
    トークンは `/auth/token` で取得してください。

    ## エラーレスポンス
    エラーは RFC 7807 Problem Details 形式で返します。
    `type` フィールドにエラー種別の URI、`errors` 配列に詳細を含めます。

    ## バージョニング
    URL パスにバージョンを含めます（例: `/v1/users`）。
    破壊的変更はメジャーバージョンを更新します。
  contact:
    email: api-support@example.com
  license:
    name: MIT

servers:
  - url: https://api.example.com/v1
    description: Production
  - url: https://api-staging.example.com/v1
    description: Staging
  - url: http://localhost:3000/v1
    description: Local Development

tags:
  - name: users
    description: ユーザーの作成・取得・更新・削除を行います。
  - name: orders
    description: 注文の作成・管理を行います。
```

---

## パス・操作定義

すべての operation に `description` を記載し、`responses` には発生しうる 4xx/5xx を全て列挙する。
レスポンスには `examples`（複数シナリオ）を定義する。

```yaml
paths:
  /users:
    get:
      summary: ユーザー一覧取得
      operationId: listUsers
      description: |
        ページネーション付きでユーザー一覧を返します。

        - `q` パラメータで名前・メールアドレスの前方一致検索が可能です。
        - デフォルトは作成日時降順で返します。
        - 管理者ロールのみ全ユーザーを取得できます。一般ユーザーは自分のデータのみ返ります。
      tags: [users]
      security:
        - bearerAuth: []
      parameters:
        - $ref: '#/components/parameters/PageCursor'
        - $ref: '#/components/parameters/PageLimit'
        - name: q
          in: query
          description: 名前・メールアドレスで絞り込み（前方一致）
          schema:
            type: string
            example: "alice"
        - name: role
          in: query
          description: ロールで絞り込み（省略時は全ロール）
          schema:
            type: string
            enum: [admin, member, viewer]
      responses:
        '200':
          description: 取得成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserListResponse'
              examples:
                multiple_users:
                  summary: 複数ユーザーの取得例
                  value:
                    data:
                      - id: "550e8400-e29b-41d4-a716-446655440000"
                        name: "Alice Smith"
                        email: "alice@example.com"
                        role: "member"
                        createdAt: "2024-01-15T09:30:00Z"
                      - id: "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
                        name: "Bob Jones"
                        email: "bob@example.com"
                        role: "admin"
                        createdAt: "2024-01-10T08:00:00Z"
                    pagination:
                      cursor: "eyJpZCI6IjZiYTdifQ=="
                      hasNext: true
                      total: 42
                    meta:
                      requestId: "req-abc-123"
                empty_result:
                  summary: 検索結果が0件の場合
                  value:
                    data: []
                    pagination:
                      cursor: null
                      hasNext: false
                      total: 0
                    meta:
                      requestId: "req-def-456"
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'

    post:
      summary: ユーザー作成
      operationId: createUser
      description: |
        新しいユーザーを作成します。

        - メールアドレスはシステム内で一意である必要があります。
        - `role` を省略した場合は `member` が設定されます。
        - 作成後に確認メールが送信されます。
      tags: [users]
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
            examples:
              basic:
                summary: 最小限の入力例
                value:
                  name: "Alice Smith"
                  email: "alice@example.com"
                  password: "SecureP@ss1"
              with_role:
                summary: ロール指定ありの例
                value:
                  name: "Bob Admin"
                  email: "bob@example.com"
                  password: "SecureP@ss2"
                  role: "admin"
      responses:
        '201':
          description: 作成成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
              examples:
                created:
                  summary: 作成されたユーザーの例
                  value:
                    data:
                      id: "550e8400-e29b-41d4-a716-446655440000"
                      name: "Alice Smith"
                      email: "alice@example.com"
                      role: "member"
                      createdAt: "2024-03-01T10:00:00Z"
                      updatedAt: "2024-03-01T10:00:00Z"
                    meta:
                      requestId: "req-ghi-789"
        '409':
          description: メールアドレスが既に使用中
          content:
            application/problem+json:
              schema:
                $ref: '#/components/schemas/ProblemDetails'
              examples:
                email_conflict:
                  summary: メールアドレス重複
                  value:
                    type: "https://api.example.com/errors/conflict"
                    title: "Conflict"
                    status: 409
                    detail: "Email address 'alice@example.com' is already registered."
                    instance: "/v1/users"
        '422':
          $ref: '#/components/responses/ValidationError'
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'

  /users/{userId}:
    parameters:
      - name: userId
        in: path
        required: true
        description: ユーザーの一意識別子（UUID v4）
        schema:
          type: string
          format: uuid
          example: "550e8400-e29b-41d4-a716-446655440000"

    get:
      summary: ユーザー取得
      operationId: getUser
      description: |
        指定された ID のユーザーを取得します。

        - 本人または管理者ロールのみアクセス可能です。
        - 論理削除済みユーザーは 404 を返します。
      tags: [users]
      security:
        - bearerAuth: []
      responses:
        '200':
          description: 取得成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
              examples:
                member_user:
                  summary: 一般ユーザーの例
                  value:
                    data:
                      id: "550e8400-e29b-41d4-a716-446655440000"
                      name: "Alice Smith"
                      email: "alice@example.com"
                      role: "member"
                      createdAt: "2024-01-15T09:30:00Z"
                      updatedAt: "2024-02-20T14:00:00Z"
                    meta:
                      requestId: "req-jkl-012"
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          description: 指定したユーザーが存在しない
          content:
            application/problem+json:
              schema:
                $ref: '#/components/schemas/ProblemDetails'
              examples:
                user_not_found:
                  summary: ユーザーが見つからない場合
                  value:
                    type: "https://api.example.com/errors/not-found"
                    title: "Not Found"
                    status: 404
                    detail: "User with id '550e8400-e29b-41d4-a716-446655440000' does not exist."
                    instance: "/v1/users/550e8400-e29b-41d4-a716-446655440000"
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'

    patch:
      summary: ユーザー部分更新
      operationId: updateUser
      description: |
        ユーザー情報を部分更新します。

        - 本人または管理者ロールのみ更新可能です。
        - `role` の変更は管理者ロールのみ可能です。
        - メールアドレス変更時は確認メールが再送信されます。
        - 少なくとも 1 フィールドを指定する必要があります。
      tags: [users]
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/UpdateUserRequest'
            examples:
              update_name:
                summary: 名前だけ更新
                value:
                  name: "Alice Johnson"
              update_email:
                summary: メールアドレス更新
                value:
                  email: "alice.new@example.com"
      responses:
        '200':
          description: 更新成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/NotFound'
        '409':
          description: メールアドレスが既に使用中（更新時）
          content:
            application/problem+json:
              schema:
                $ref: '#/components/schemas/ProblemDetails'
        '422':
          $ref: '#/components/responses/ValidationError'
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'

    delete:
      summary: ユーザー削除
      operationId: deleteUser
      description: |
        ユーザーを論理削除します。

        - 管理者ロールのみ実行可能です。
        - 削除後は同一 ID での取得が 404 を返します。
        - 関連する注文データは保持されます（物理削除ではありません）。
      tags: [users]
      security:
        - bearerAuth: []
      responses:
        '204':
          description: 削除成功（レスポンスボディなし）
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          $ref: '#/components/responses/NotFound'
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          $ref: '#/components/responses/InternalServerError'
```

---

## スキーマ定義

すべてのスキーマフィールドに `description` を記載し、スキーマレベルに `example` を定義する。

```yaml
components:
  schemas:
    # エンティティ
    User:
      type: object
      required: [id, name, email, role, createdAt, updatedAt]
      description: ユーザーエンティティ
      properties:
        id:
          type: string
          format: uuid
          readOnly: true
          description: ユーザーの一意識別子（UUID v4）
        name:
          type: string
          minLength: 1
          maxLength: 100
          description: ユーザーの表示名
        email:
          type: string
          format: email
          description: ログインに使用するメールアドレス。システム内で一意。
        role:
          type: string
          enum: [admin, member, viewer]
          description: |
            ユーザーのロール。
            - `admin`: 全リソースへのフルアクセス
            - `member`: 自分のリソースへの読み書き
            - `viewer`: 読み取り専用
        createdAt:
          type: string
          format: date-time
          readOnly: true
          description: レコード作成日時（UTC, ISO 8601）
        updatedAt:
          type: string
          format: date-time
          readOnly: true
          description: レコード最終更新日時（UTC, ISO 8601）
      example:
        id: "550e8400-e29b-41d4-a716-446655440000"
        name: "Alice Smith"
        email: "alice@example.com"
        role: "member"
        createdAt: "2024-01-15T09:30:00Z"
        updatedAt: "2024-02-20T14:00:00Z"

    # リクエスト
    CreateUserRequest:
      type: object
      required: [name, email, password]
      description: ユーザー作成リクエスト
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 100
          description: ユーザーの表示名
        email:
          type: string
          format: email
          description: ログインに使用するメールアドレス
        password:
          type: string
          minLength: 8
          writeOnly: true
          description: パスワード（8文字以上、英数字・記号を含む）
        role:
          type: string
          enum: [admin, member, viewer]
          default: member
          description: ユーザーのロール（省略時は `member`）
      example:
        name: "Alice Smith"
        email: "alice@example.com"
        password: "SecureP@ss1"
        role: "member"

    UpdateUserRequest:
      type: object
      minProperties: 1
      description: ユーザー部分更新リクエスト。少なくとも 1 フィールドを指定すること。
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 100
          description: 変更後の表示名
        email:
          type: string
          format: email
          description: 変更後のメールアドレス（確認メールが再送信される）
      example:
        name: "Alice Johnson"

    # レスポンスラッパー
    UserResponse:
      type: object
      required: [data]
      description: 単一ユーザーのレスポンスラッパー
      properties:
        data:
          $ref: '#/components/schemas/User'
        meta:
          $ref: '#/components/schemas/ResponseMeta'
      example:
        data:
          id: "550e8400-e29b-41d4-a716-446655440000"
          name: "Alice Smith"
          email: "alice@example.com"
          role: "member"
          createdAt: "2024-01-15T09:30:00Z"
          updatedAt: "2024-02-20T14:00:00Z"
        meta:
          requestId: "req-abc-123"

    UserListResponse:
      type: object
      required: [data, pagination]
      description: ユーザー一覧のレスポンスラッパー
      properties:
        data:
          type: array
          description: ユーザーの配列
          items:
            $ref: '#/components/schemas/User'
        pagination:
          $ref: '#/components/schemas/CursorPagination'
        meta:
          $ref: '#/components/schemas/ResponseMeta'

    # 共通
    ResponseMeta:
      type: object
      description: レスポンスメタデータ
      properties:
        requestId:
          type: string
          description: リクエストの追跡 ID（ログ・問い合わせ時に使用）
          example: "req-abc-123"

    CursorPagination:
      type: object
      required: [hasNext]
      description: カーソルベースページネーション情報
      properties:
        cursor:
          type: string
          nullable: true
          description: 次ページ取得用カーソル。最終ページの場合は null。
          example: "eyJpZCI6IjZiYTdifQ=="
        hasNext:
          type: boolean
          description: 次ページが存在するか
          example: true
        total:
          type: integer
          description: フィルタ条件に一致する全件数
          example: 42

    OffsetPagination:
      type: object
      required: [page, perPage, total]
      description: オフセットベースページネーション情報
      properties:
        page:
          type: integer
          description: 現在のページ番号（1始まり）
          example: 2
        perPage:
          type: integer
          description: 1ページあたりの件数
          example: 25
        total:
          type: integer
          description: 全件数
          example: 100
        totalPages:
          type: integer
          description: 総ページ数
          example: 4

    # RFC 7807 Problem Details
    ProblemDetails:
      type: object
      required: [type, title, status]
      description: |
        RFC 7807 Problem Details 形式のエラーレスポンス。
        `type` にエラー種別の URI、`errors` 配列にフィールドレベルの詳細を含む。
      properties:
        type:
          type: string
          format: uri
          description: エラー種別を識別する URI
          example: "https://api.example.com/errors/validation-error"
        title:
          type: string
          description: エラーの短い説明（人間が読める）
          example: "Validation Error"
        status:
          type: integer
          description: HTTP ステータスコード
          example: 422
        detail:
          type: string
          description: エラーの詳細説明
          example: "The request body contains invalid fields."
        instance:
          type: string
          description: エラーが発生したリソースの URI
          example: "/v1/users/register"
        errors:
          type: array
          description: フィールドレベルのバリデーションエラー詳細
          items:
            type: object
            properties:
              field:
                type: string
                description: エラーが発生したフィールド名
                example: "email"
              message:
                type: string
                description: フィールドレベルのエラーメッセージ
                example: "Invalid email format"
      example:
        type: "https://api.example.com/errors/validation-error"
        title: "Validation Error"
        status: 422
        detail: "The request body contains invalid fields."
        instance: "/v1/users"
        errors:
          - field: "email"
            message: "Invalid email format"
          - field: "name"
            message: "Name must not be empty"
```

---

## 認証定義

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
      description: |
        JWT Bearer トークン認証。
        `Authorization: Bearer <token>` ヘッダーで送信してください。
        トークンは `/auth/token` エンドポイントで取得できます。

    apiKey:
      type: apiKey
      in: header
      name: X-API-Key
      description: |
        API Key 認証（サーバー間通信用）。
        `X-API-Key: <key>` ヘッダーで送信してください。

    oauth2:
      type: oauth2
      description: OAuth 2.0 認証（サードパーティ連携用）
      flows:
        authorizationCode:
          authorizationUrl: https://auth.example.com/oauth/authorize
          tokenUrl: https://auth.example.com/oauth/token
          scopes:
            read:users: ユーザー情報の読み取り
            write:users: ユーザー情報の書き込み
            admin: 管理者操作

# グローバルセキュリティ設定（全エンドポイントに適用）
security:
  - bearerAuth: []
```

---

## ページネーション

```yaml
components:
  parameters:
    PageCursor:
      name: after
      in: query
      description: |
        カーソルベースページネーションのカーソル値。
        前のレスポンスの `pagination.cursor` を指定します。省略時は先頭から取得します。
      schema:
        type: string
        example: "eyJpZCI6IjZiYTdifQ=="

    PageLimit:
      name: limit
      in: query
      description: 1ページあたりの取得件数（1〜100、デフォルト 20）
      schema:
        type: integer
        minimum: 1
        maximum: 100
        default: 20
        example: 20

    PageNumber:
      name: page
      in: query
      description: ページ番号（1始まり、デフォルト 1）
      schema:
        type: integer
        minimum: 1
        default: 1
        example: 1

    PageSize:
      name: perPage
      in: query
      description: 1ページあたりの件数（1〜100、デフォルト 25）
      schema:
        type: integer
        minimum: 1
        maximum: 100
        default: 25
        example: 25
```

---

## エラーレスポンス

各 `responses` エントリに `examples`（複数シナリオ）を定義することで、仕様書読者が各エラーの発生条件を理解できるようにする。

```yaml
components:
  responses:
    BadRequest:
      description: リクエスト不正（パラメータ形式エラーなど）
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            invalid_param:
              summary: クエリパラメータが不正
              value:
                type: "https://api.example.com/errors/bad-request"
                title: "Bad Request"
                status: 400
                detail: "Query parameter 'limit' must be a positive integer."
                instance: "/v1/users"

    Unauthorized:
      description: 認証トークンが無効または未提供
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            missing_token:
              summary: Authorization ヘッダーが未提供
              value:
                type: "https://api.example.com/errors/unauthorized"
                title: "Unauthorized"
                status: 401
                detail: "Authorization header is missing."
                instance: "/v1/users"
            expired_token:
              summary: JWT トークンの有効期限切れ
              value:
                type: "https://api.example.com/errors/unauthorized"
                title: "Unauthorized"
                status: 401
                detail: "Token has expired. Please re-authenticate."
                instance: "/v1/users"
            invalid_token:
              summary: JWT トークンが不正（署名不一致など）
              value:
                type: "https://api.example.com/errors/unauthorized"
                title: "Unauthorized"
                status: 401
                detail: "Token signature is invalid."
                instance: "/v1/users"

    Forbidden:
      description: 認証済みだが操作に必要な権限がない
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            insufficient_role:
              summary: 必要なロールがない
              value:
                type: "https://api.example.com/errors/forbidden"
                title: "Forbidden"
                status: 403
                detail: "Admin role is required to delete users."
                instance: "/v1/users/550e8400-e29b-41d4-a716-446655440000"
            not_owner:
              summary: 他のユーザーのリソースへのアクセス
              value:
                type: "https://api.example.com/errors/forbidden"
                title: "Forbidden"
                status: 403
                detail: "You can only access your own resources."
                instance: "/v1/users/6ba7b810-9dad-11d1-80b4-00c04fd430c8"

    NotFound:
      description: 指定したリソースが存在しない、または削除済み
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            resource_not_found:
              summary: リソースが見つからない
              value:
                type: "https://api.example.com/errors/not-found"
                title: "Not Found"
                status: 404
                detail: "The requested resource was not found."
                instance: "/v1/users/550e8400-e29b-41d4-a716-446655440000"

    Conflict:
      description: リソースの競合（重複・楽観ロック失敗など）
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            duplicate_email:
              summary: メールアドレス重複
              value:
                type: "https://api.example.com/errors/conflict"
                title: "Conflict"
                status: 409
                detail: "Email address 'alice@example.com' is already registered."
                instance: "/v1/users"
            optimistic_lock:
              summary: 楽観ロック失敗（他のリクエストが先に更新した場合）
              value:
                type: "https://api.example.com/errors/conflict"
                title: "Conflict"
                status: 409
                detail: "Resource has been modified by another request. Please refresh and retry."
                instance: "/v1/users/550e8400-e29b-41d4-a716-446655440000"

    ValidationError:
      description: リクエストボディのバリデーションエラー
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            multiple_fields:
              summary: 複数フィールドのバリデーションエラー
              value:
                type: "https://api.example.com/errors/validation-error"
                title: "Validation Error"
                status: 422
                detail: "The request body contains invalid fields."
                instance: "/v1/users"
                errors:
                  - field: "email"
                    message: "Invalid email format"
                  - field: "password"
                    message: "Password must be at least 8 characters"
            single_field:
              summary: 単一フィールドのバリデーションエラー
              value:
                type: "https://api.example.com/errors/validation-error"
                title: "Validation Error"
                status: 422
                detail: "The request body contains invalid fields."
                instance: "/v1/users"
                errors:
                  - field: "name"
                    message: "Name must not be empty"

    TooManyRequests:
      description: レートリミット超過。`Retry-After` ヘッダーの秒数後に再試行してください。
      headers:
        X-RateLimit-Limit:
          description: 時間窓あたりの最大リクエスト数
          schema:
            type: integer
            example: 1000
        X-RateLimit-Remaining:
          description: 現在の時間窓での残りリクエスト数
          schema:
            type: integer
            example: 0
        X-RateLimit-Reset:
          description: レートリミットがリセットされる Unix タイムスタンプ
          schema:
            type: integer
            example: 1700000060
        Retry-After:
          description: 次のリクエストが可能になるまでの秒数
          schema:
            type: integer
            example: 60
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            rate_limited:
              summary: レートリミット超過
              value:
                type: "https://api.example.com/errors/too-many-requests"
                title: "Too Many Requests"
                status: 429
                detail: "Rate limit exceeded. Please retry after 60 seconds."
                instance: "/v1/users"

    InternalServerError:
      description: サーバー内部エラー。内部情報は含みません。
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            server_error:
              summary: サーバー内部エラー
              value:
                type: "https://api.example.com/errors/internal-server-error"
                title: "Internal Server Error"
                status: 500
                detail: "An unexpected error occurred. Please contact support with the requestId."
                instance: "/v1/users"

    ServiceUnavailable:
      description: サービス一時停止（メンテナンス・過負荷）
      headers:
        Retry-After:
          description: サービス再開予定までの秒数
          schema:
            type: integer
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          examples:
            maintenance:
              summary: メンテナンス中
              value:
                type: "https://api.example.com/errors/service-unavailable"
                title: "Service Unavailable"
                status: 503
                detail: "The service is under maintenance. Please retry after 300 seconds."
                instance: "/v1/users"
```
