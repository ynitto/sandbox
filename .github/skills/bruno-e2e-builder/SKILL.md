---
name: bruno-e2e-builder
description: OpenAPI仕様を元にBruno CLI実行可能なE2Eテストファイルを生成するスキル。「BrunoのE2Eテストを作って」「OpenAPIからBrunoテストを生成して」「Bruno用のE2Eを作成して」「APIテストをBrunoで書いて」「bruファイルを生成して」などで発動する。
metadata:
  version: 1.0.0
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
│       ├── 001. {summary}.bru      # 1エンドポイント = 1ファイル
│       └── 002. {summary}.bru
└── scenario/                       # シナリオテスト（手動 or スキャフォールド）
    └── {scenario-name}/
        ├── 001. setup - {purpose}.bru
        ├── 002. {main-action}.bru
        └── 003. teardown - {purpose}.bru
```

**ファイル命名規則:** `{連番3桁}. {タイトル}.bru`
連番の昇順 = Bruno CLIの実行順 = bru ファイルに記述した `seq` の値。

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
- `e2e/api/{tag}/*.bru` - OpenAPIのoperationごとに1ファイル

## ステップ2: シナリオテストのスキャフォールド（任意）

業務フローの結合テストを生成する:

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

## ステップ3: 環境変数の設定

生成された `e2e/environments/local.bru` を編集:

```bru
vars {
  baseUrl: http://localhost:8080
}

vars:secret [
  accessToken
]
```

## ステップ4: Bruno CLIで実行

```bash
# インストール（初回のみ）
npm install -g @usebruno/cli

# APIテスト全件
bru run e2e/api --env local --recursive

# 特定タグのみ
bru run e2e/api/users --env local

# シナリオテスト
bru run e2e/scenario/user-registration --env local

# 全E2E
bru run e2e --env local --recursive
```

## 生成後の確認・修正ポイント

スクリプトはスケルトンを生成する。以下を確認して補完する:

1. **アサーション** - 生成テストはステータスコードのみ。レスポンスボディの検証を追加する
2. **変数の引き継ぎ** - `script:post-response` ブロックで、作成したリソースのIDを後続ステップへ渡す
3. **認証** - `accessToken` は `vars:secret` 管理。Bearer token以外が必要なら `auth {}` ブロックを使う
4. **シナリオ順序** - スキャフォールドはテンプレートのみ。実際のフローに合わせて調整する

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
