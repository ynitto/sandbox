# OpenAPI 3.0 スニペット集

SKILL.md の Step 5 で REST API を選択した場合に読み込む。

## 目次

1. [ベース構造](#ベース構造)
2. [パス・操作定義](#パス操作定義)
3. [スキーマ定義](#スキーマ定義)
4. [認証定義](#認証定義)
5. [ページネーション](#ページネーション)
6. [エラーレスポンス](#エラーレスポンス)

---

## ベース構造

```yaml
openapi: 3.0.3
info:
  title: Example API
  version: 1.0.0
  description: |
    Example API の説明。
  contact:
    email: api-support@example.com
  license:
    name: MIT

servers:
  - url: https://api.example.com/v1
    description: Production
  - url: https://api-staging.example.com/v1
    description: Staging

tags:
  - name: users
    description: ユーザー管理
  - name: orders
    description: 注文管理
```

---

## パス・操作定義

```yaml
paths:
  /users:
    get:
      summary: ユーザー一覧取得
      operationId: listUsers
      tags: [users]
      security:
        - bearerAuth: []
      parameters:
        - $ref: '#/components/parameters/PageCursor'
        - $ref: '#/components/parameters/PageLimit'
        - name: q
          in: query
          description: 名前・メールで絞り込み
          schema:
            type: string
      responses:
        '200':
          description: 成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserListResponse'
        '401':
          $ref: '#/components/responses/Unauthorized'

    post:
      summary: ユーザー作成
      operationId: createUser
      tags: [users]
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
      responses:
        '201':
          description: 作成成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
        '422':
          $ref: '#/components/responses/ValidationError'

  /users/{userId}:
    parameters:
      - name: userId
        in: path
        required: true
        schema:
          type: string
          format: uuid
    get:
      summary: ユーザー取得
      operationId: getUser
      tags: [users]
      security:
        - bearerAuth: []
      responses:
        '200':
          description: 成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
        '404':
          $ref: '#/components/responses/NotFound'
    patch:
      summary: ユーザー部分更新
      operationId: updateUser
      tags: [users]
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/UpdateUserRequest'
      responses:
        '200':
          description: 更新成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
        '422':
          $ref: '#/components/responses/ValidationError'
    delete:
      summary: ユーザー削除
      operationId: deleteUser
      tags: [users]
      security:
        - bearerAuth: []
      responses:
        '204':
          description: 削除成功
        '404':
          $ref: '#/components/responses/NotFound'
```

---

## スキーマ定義

```yaml
components:
  schemas:
    # エンティティ
    User:
      type: object
      required: [id, name, email, createdAt]
      properties:
        id:
          type: string
          format: uuid
          readOnly: true
        name:
          type: string
          minLength: 1
          maxLength: 100
        email:
          type: string
          format: email
        role:
          type: string
          enum: [admin, member, viewer]
          default: member
        createdAt:
          type: string
          format: date-time
          readOnly: true
        updatedAt:
          type: string
          format: date-time
          readOnly: true

    # リクエスト
    CreateUserRequest:
      type: object
      required: [name, email, password]
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 100
        email:
          type: string
          format: email
        password:
          type: string
          minLength: 8
          writeOnly: true
        role:
          type: string
          enum: [admin, member, viewer]
          default: member

    UpdateUserRequest:
      type: object
      minProperties: 1
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 100
        email:
          type: string
          format: email

    # レスポンスラッパー
    UserResponse:
      type: object
      required: [data]
      properties:
        data:
          $ref: '#/components/schemas/User'
        meta:
          $ref: '#/components/schemas/ResponseMeta'

    UserListResponse:
      type: object
      required: [data, pagination]
      properties:
        data:
          type: array
          items:
            $ref: '#/components/schemas/User'
        pagination:
          $ref: '#/components/schemas/CursorPagination'
        meta:
          $ref: '#/components/schemas/ResponseMeta'

    # 共通
    ResponseMeta:
      type: object
      properties:
        requestId:
          type: string

    CursorPagination:
      type: object
      required: [hasNext]
      properties:
        cursor:
          type: string
          nullable: true
          description: 次ページ取得用カーソル
        hasNext:
          type: boolean
        total:
          type: integer

    # RFC 7807 Problem Details
    ProblemDetails:
      type: object
      required: [type, title, status]
      properties:
        type:
          type: string
          format: uri
        title:
          type: string
        status:
          type: integer
        detail:
          type: string
        instance:
          type: string
        errors:
          type: array
          items:
            type: object
            properties:
              field:
                type: string
              message:
                type: string
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

    apiKey:
      type: apiKey
      in: header
      name: X-API-Key

    oauth2:
      type: oauth2
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
      description: カーソルベースページネーション（前ページの最終カーソル値）
      schema:
        type: string

    PageLimit:
      name: limit
      in: query
      description: 取得件数（最大100）
      schema:
        type: integer
        minimum: 1
        maximum: 100
        default: 20

    PageNumber:
      name: page
      in: query
      schema:
        type: integer
        minimum: 1
        default: 1

    PageSize:
      name: perPage
      in: query
      schema:
        type: integer
        minimum: 1
        maximum: 100
        default: 25
```

---

## エラーレスポンス

```yaml
components:
  responses:
    BadRequest:
      description: リクエスト不正
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          example:
            type: https://api.example.com/errors/bad-request
            title: Bad Request
            status: 400

    Unauthorized:
      description: 未認証
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          example:
            type: https://api.example.com/errors/unauthorized
            title: Unauthorized
            status: 401
            detail: JWT token is missing or invalid.

    Forbidden:
      description: 権限不足
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'

    NotFound:
      description: リソース未発見
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          example:
            type: https://api.example.com/errors/not-found
            title: Not Found
            status: 404
            detail: The requested resource was not found.

    Conflict:
      description: 競合（重複など）
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'

    ValidationError:
      description: バリデーションエラー
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
          example:
            type: https://api.example.com/errors/validation-error
            title: Validation Error
            status: 422
            detail: The request body contains invalid fields.
            errors:
              - field: email
                message: Invalid email format
              - field: name
                message: Name must not be empty

    TooManyRequests:
      description: レートリミット超過
      headers:
        X-RateLimit-Limit:
          schema:
            type: integer
        X-RateLimit-Remaining:
          schema:
            type: integer
        X-RateLimit-Reset:
          schema:
            type: integer
      content:
        application/problem+json:
          schema:
            $ref: '#/components/schemas/ProblemDetails'
```
