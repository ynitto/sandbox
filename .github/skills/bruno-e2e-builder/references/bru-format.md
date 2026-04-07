# BRU形式リファレンス

Bruno の `.bru` ファイル形式（BRU言語）の詳細リファレンス。

---

## ブロック一覧

| ブロック | 必須 | 説明 |
|---|---|---|
| `meta {}` | ✅ | ファイルのメタ情報（name, type, seq） |
| `get/post/put/patch/delete {}` | ✅ | HTTPメソッド・URL・body/auth種別 |
| `headers {}` | 任意 | リクエストヘッダー |
| `params:query {}` | 任意 | クエリパラメータ（`~`プレフィックス = オプション） |
| `body:json {}` | 任意 | JSONリクエストボディ |
| `body:text {}` | 任意 | プレーンテキストボディ |
| `body:form-urlencoded {}` | 任意 | フォームデータ |
| `auth:bearer {}` | 任意 | Bearer認証（headerに書く場合は不要） |
| `script:pre-request {}` | 任意 | リクエスト前に実行するJS |
| `script:post-response {}` | 任意 | レスポンス後に実行するJS |
| `tests {}` | 任意 | テストアサーション（Chai.js） |
| `vars {}` | 任意 | 静的変数（環境ファイル用） |
| `vars:secret []` | 任意 | シークレット変数名リスト |

---

## 完全なGETリクエスト例

```bru
meta {
  name: Get User by ID
  type: http
  seq: 1
}

get {
  url: {{baseUrl}}/api/users/{{userId}}
  body: none
  auth: none
}

params:query {
  include: profile
  ~fields: id,name,email
}

headers {
  Authorization: Bearer {{accessToken}}
  Accept: application/json
}

tests {
  test("should return 200", function() {
    expect(res.status).to.equal(200);
  });

  test("should have user id", function() {
    const body = res.getBody();
    expect(body).to.have.property("id");
    expect(body.id).to.equal(bru.getVar("userId"));
  });
}

script:post-response {
  bru.setVar("userEmail", res.body.email);
}
```

---

## POST / PUT / PATCH（JSONボディ）

```bru
meta {
  name: Create User
  type: http
  seq: 2
}

post {
  url: {{baseUrl}}/api/users
  body: json
  auth: none
}

headers {
  Content-Type: application/json
  Authorization: Bearer {{accessToken}}
}

body:json {
  {
    "name": "Test User",
    "email": "test@example.com",
    "role": "viewer"
  }
}

tests {
  test("should return 201", function() {
    expect(res.status).to.equal(201);
  });

  test("should return created user", function() {
    expect(res.body).to.have.property("id");
    expect(res.body.email).to.equal("test@example.com");
  });
}

script:post-response {
  bru.setVar("createdUserId", res.body.id);
}
```

---

## DELETEリクエスト（前のステップの変数を使用）

```bru
meta {
  name: teardown - Delete User
  type: http
  seq: 4
}

delete {
  url: {{baseUrl}}/api/users/{{createdUserId}}
  body: none
  auth: none
}

headers {
  Authorization: Bearer {{accessToken}}
}

tests {
  test("should return 204", function() {
    expect(res.status).to.equal(204);
  });
}
```

---

## 環境ファイル（environments/local.bru）

```bru
vars {
  baseUrl: http://localhost:8080
}

vars:secret [
  accessToken
]
```

シークレット変数 (`vars:secret`) の値はファイルに保存されず、実行時に環境変数または Bruno GUI で設定する。

CLI実行時に環境変数で渡す例:
```bash
accessToken=my-token bru run e2e/api --env local
```

---

## bruno.json（コレクション設定）

```json
{
  "version": "1",
  "name": "My API Tests",
  "type": "collection"
}
```

---

## テストアサーション（Chai.js）

```javascript
// ステータスコード
expect(res.status).to.equal(200);
expect(res.status).to.be.oneOf([200, 201]);

// レスポンスボディ
expect(res.body).to.have.property("id");
expect(res.body.items).to.be.an("array").that.has.length.above(0);
expect(res.body.name).to.equal("expected name");

// getBody() でも取得可能
const body = res.getBody();
expect(body.total).to.be.a("number");
```

---

## 変数操作（script ブロック）

```javascript
// 変数の設定（後続ステップで {{varName}} として使用可能）
bru.setVar("resourceId", res.body.id);
bru.setVar("token", res.body.access_token);

// 変数の取得
const id = bru.getVar("resourceId");

// 環境変数の取得
const url = bru.getEnvVar("baseUrl");
```

---

## Bruno CLIコマンド

```bash
# インストール
npm install -g @usebruno/cli

# ディレクトリ内全テストを実行
bru run e2e/api/users --env local

# 再帰的に全テストを実行（alphabetical順）
bru run e2e --env local --recursive

# 特定ファイルのみ
bru run e2e/api/users/"001. Get Users.bru" --env local

# JUnit形式でレポート出力
bru run e2e --env local --recursive --reporter junit --output ./reports/results.xml
```

---

## ファイル命名とシーケンス制御

`seq` メタフィールドはBruno GUIでの順序に使用されるが、CLIの実行順はファイル名のアルファベット昇順。

このスキルでは `001. タイトル.bru` の命名規則により、ファイル昇順 = 実行順序が一致するようにする。
