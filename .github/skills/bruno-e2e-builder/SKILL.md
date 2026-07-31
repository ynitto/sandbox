---
name: bruno-e2e-builder
description: OpenAPI仕様を元にBruno CLI実行可能なE2Eテストファイルを生成するスキル。「BrunoのE2Eテストを作って」「OpenAPIからBrunoテストを生成して」「Bruno用のE2Eを作成して」「APIテストをBrunoで書いて」「bruファイルを生成して」「テストを追加して」などで発動する。
metadata:
  version: 2.0.0
  tier: experimental
  category: testing
  tags:
    - bruno
    - e2e-testing
    - openapi
    - api-testing
---

# bruno-e2e-builder

OpenAPI仕様からBruno CLI実行可能なE2Eテストファイルを生成する。

## 生成されるフォルダ構造

```
e2e/
├── bruno.json                      # コレクション設定（自動生成）
├── environments/
│   └── local.bru                   # 環境変数（自動生成）
├── api/                            # APIインターフェーステスト（自動生成）
│   └── {tag}/
│       └── {method}-{path}/        # エンドポイント毎のフォルダ
│           ├── 001. {summary}.bru  # 正常系テスト（シーケンシャル、001-099）
│           ├── 002. {summary} - variant.bru  # 追加の正常系（補完で追加）
│           ├── 101. error - 400 Bad Request.bru  # エラー系（単発、101-199）
│           └── 102. error - 401 Unauthorized.bru # エラー系（単発）
└── scenario/                       # シナリオテスト（手動 or スキャフォールド）
    └── {use-case}/                 # ユースケース毎のフォルダ（リソースを跨ぐ操作）
        ├── 001. setup - {step}.bru # 1エンドポイント = 1ファイル
        ├── 002. {step}.bru
        └── 003. teardown - {step}.bru
```

**ファイル命名規則:** `{連番3桁}. {タイトル}.bru`
連番の昇順 = Bruno CLIの実行順 = bru ファイルに記述した `seq` の値。

**api/ の2種類のテスト:**
- **正常系（001-099）**: 1エンドポイントのみ呼ぶ。シーケンシャルに実行（後のテストが前の結果を使うこともある）
- **エラー系（101-199）**: 1エンドポイントのみ呼ぶ。独立した単発テスト（順序不問）

**scenario/ の役割:**
- RESTリソースを複数跨いで操作する**ユースケース単位**でフォルダを作る
- フォルダ内に1エンドポイント1ファイルを並べる

## ステップ1: APIテストの自動生成

OpenAPIファイルから `e2e/api/` 配下のテストを一括生成する:

```bash
python .github/skills/bruno-e2e-builder/scripts/generate_e2e.py <openapi_file> [options]
```

| オプション | デフォルト | 説明 |
|---|---|---|
| `openapi_file` | (必須) | OpenAPI仕様ファイル（YAML/JSON） |
| `--output-dir` | `./e2e` | 出力先ディレクトリ |
| `--env` | `local` | 環境ファイル名 |
| `--base-url` | OpenAPIのservers[0] | ベースURL（上書き） |

```bash
# 最小実行
python .github/skills/bruno-e2e-builder/scripts/generate_e2e.py openapi.yaml

# 環境・URL指定
python .github/skills/bruno-e2e-builder/scripts/generate_e2e.py api/spec.yaml \
  --output-dir ./e2e --env staging --base-url https://staging.example.com
```

生成物:
- `e2e/bruno.json` - コレクション設定
- `e2e/environments/{env}.bru` - 環境変数テンプレート
- `e2e/api/{tag}/{method}-{path}/*.bru` - エンドポイント毎に正常系1件＋OpenAPIのエラー定義から複数件

## ステップ2: シナリオテストのスキャフォールド（任意）

RESTリソースを複数跨ぐユースケースの結合テストを生成する:

```bash
python .github/skills/bruno-e2e-builder/scripts/scaffold_scenario.py \
  --name user-registration \
  --output-dir ./e2e/scenario \
  --step "setup create-test-data POST /users" \
  --step "main register-user POST /auth/register" \
  --step "verify get-profile GET /users/{id}" \
  --step "teardown delete-user DELETE /users/{id}"
```

ステップ形式: `"<type> <name> <METHOD> <path>"`
- `type`: `setup` / `main` / `verify` / `teardown`（ファイル名プレフィックスに使用）

生成物: `e2e/scenario/{name}/001. setup - create-test-data.bru` ... など

各ファイルは1エンドポイントのみを呼ぶ。ユースケース内のリソース操作の流れを表現する。

## ステップ3: テストの補完（ユーザー指示による追加）

自動生成後、不足しているテストケースを指示に応じて追加できる:

```bash
python .github/skills/bruno-e2e-builder/scripts/add_test.py \
  --dir <エンドポイントフォルダ> \
  --type normal|error|scenario \
  --name <テスト名> \
  --method <METHOD> \
  --path <path> \
  [--status <code>]
```

| オプション | 説明 |
|---|---|
| `--dir` | 追加先フォルダ（例: `e2e/api/users/post-users`） |
| `--type` | `normal`（正常系, 001-099）/ `error`（エラー系, 101-199）/ `scenario`（シナリオステップ） |
| `--name` | テスト名（ファイル名と meta.name に使用） |
| `--method` | HTTPメソッド |
| `--path` | APIパス |
| `--status` | 期待するHTTPステータスコード（省略時は自動決定） |

```bash
# 正常系のバリエーションを追加（オプションフィールドあり）
python .github/skills/bruno-e2e-builder/scripts/add_test.py \
  --dir e2e/api/users/post-users \
  --type normal \
  --name "Create User - with optional fields" \
  --method POST --path /users --status 201

# エラー系を追加（403 Forbidden）
python .github/skills/bruno-e2e-builder/scripts/add_test.py \
  --dir e2e/api/users/post-users \
  --type error \
  --name "403 Forbidden" \
  --method POST --path /users --status 403

# シナリオにステップを追加
python .github/skills/bruno-e2e-builder/scripts/add_test.py \
  --dir e2e/scenario/user-registration \
  --type scenario \
  --name "verify - get profile" \
  --method GET --path /users/{id} --status 200
```

シーケンス番号は既存ファイルを自動スキャンして次の空き番号が割り当てられる。

### 補完のワークフロー

ユーザーから「〇〇のテストが足りない」と言われたとき:

1. **対象フォルダを特定**: `e2e/api/{tag}/{method}-{path}/` または `e2e/scenario/{use-case}/`
2. **既存ファイルを確認**: `Glob` ツールで `*.bru` を列挙し、不足しているケースを把握
3. **`add_test.py` を実行**: 必要な `--type`, `--name`, `--status` を指定して追加
4. **TODOを埋める**: 生成されたファイルを `Edit` ツールでリクエストボディ・アサーションを補完

## ステップ4: 環境変数の設定

生成された `e2e/environments/local.bru` を編集:

```bru
vars {
  baseUrl: http://localhost:8080
}

vars:secret [
  accessToken
]
```

## ステップ5: Bruno CLIで実行

```bash
# インストール（初回のみ）
npm install -g @usebruno/cli

# 特定エンドポイントのテスト全件
bru run e2e/api/users/get-users --env local

# タグ（リソース）配下のテスト全件
bru run e2e/api/users --env local --recursive

# APIテスト全件
bru run e2e/api --env local --recursive

# シナリオテスト
bru run e2e/scenario/user-registration --env local

# 全E2E
bru run e2e --env local --recursive
```

## 生成後の確認・修正ポイント

スクリプトはスケルトンを生成する。以下を確認して補完する:

1. **アサーション** - 正常系はステータスコードのみ。レスポンスボディの検証を追加する
2. **エラーボディ** - エラー系の `body:json` は `// TODO` のまま。エラーを引き起こす値を入力する
3. **変数の引き継ぎ** - `script:post-response` ブロックで、作成したリソースのIDを後続ステップへ渡す
4. **認証** - `accessToken` は `vars:secret` 管理。Bearer token以外が必要なら `auth {}` ブロックを使う
5. **シナリオ順序** - スキャフォールドはテンプレートのみ。実際のフローに合わせて調整する

## BRUファイル形式リファレンス

詳細: [references/bru-format.md](references/bru-format.md)

### GET リクエスト（最小構成）

```bru
meta {
  name: Get Users
  type: http
  seq: 1
}

get {
  url: {{baseUrl}}/api/users
  body: none
  auth: none
}

headers {
  Authorization: Bearer {{accessToken}}
}

tests {
  test("should return 200", function() {
    expect(res.status).to.equal(200);
  });
}
```

### POST リクエスト（JSONボディ + 変数引き渡し）

```bru
meta {
  name: Create User
  type: http
  seq: 1
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
    "email": "test@example.com"
  }
}

tests {
  test("should return 201", function() {
    expect(res.status).to.equal(201);
  });
}

script:post-response {
  bru.setVar("createdUserId", res.body.id);
}
```

### エラー系テスト例（401 Unauthorized）

```bru
meta {
  name: error - 401 Unauthorized
  type: http
  seq: 101
}

post {
  url: {{baseUrl}}/api/users
  body: json
  auth: none
}

headers {
  Content-Type: application/json
}

body:json {
  {
    "name": "Test User",
    "email": "test@example.com"
  }
}

tests {
  test("should return 401", function() {
    expect(res.status).to.equal(401);
  });
}
```
