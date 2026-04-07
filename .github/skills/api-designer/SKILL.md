---
name: api-designer
description: REST API の設計・OpenAPI 仕様生成・既存仕様レビューを支援する。「APIを設計して」「OpenAPIを作って」「REST APIのエンドポイントを決めて」「ドメインモデルからAPIを設計して」「既存のOpenAPI仕様を見直して」「実装コードからOpenAPIを生成して」「APIのバージョニング戦略を決めて」などで必ず使う。
metadata:
  version: 2.1.0
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
- Step 1 の入力収集はユーザーと対話しながら行う
- Step 2 の制約分析は入力の種類によって判断基準が異なる。必ずテンプレートに従い明示する
- 互換性ポリシー（breaking / non-breaking / deprecation）は Step 3 と Step 6 の両方で確認する
- **REST 原則への違反は入力・設計・レビューのあらゆる段階で検出し、必ずユーザーに警告する**（既存インターフェースでも例外なし）
- Step 6 のセキュリティ最小チェックと OpenAPI ドキュメント品質チェックは省略しない

---

## API 設計フロー

### Step 1: 入力収集とインプット確認

以下の入力を**ユーザーと対話しながら**確認・収集する。複数受け付けてよい。

#### 確認する入力（優先順位順）

1. **openapi_spec** — 既存の OpenAPI 仕様ファイル（YAML/JSON）
2. **implementation_code** — 既存の実装コード（ルーティング定義、コントローラ等）
3. **domain_model** — ドメインモデル（Mermaid classDiagram 等）
4. **api_description** — 上記がない場合のフォールバック（API の目的・概要）

入力が揃ったら追加確認（「未定」で可）:

```
1. API の利用者（SPA / モバイル / サードパーティ / 社内システム）
2. 認証・認可方式（JWT / OAuth2 / API Key / Session など）
3. バージョニング方針（新規 / 既存バージョン継続 / メジャー更新）
4. PII・機微情報の有無（氏名、メール、決済情報など）
5. レートリミット・SLA 要件（あれば）
```

**Step 1 の出力**: `収集した入力サマリー`（種類・内容・有無）

---

### Step 2: 入力分析と制約判断

#### 入力種別ごとの制約ルール

| 入力種別 | インターフェース制約 | 品質チェック |
|----------|-------------------|------------|
| openapi_spec | **原則変更不可**（パス・メソッド・構造を維持。追加は可。削除・変更は breaking change としてユーザー承認を得る） | **必須**（境界条件・エラーポリシー・矛盾・曖昧さ・REST 原則をチェックしてフィードバック） |
| implementation_code | **コスト考慮**（変更量が大きい場合はユーザーに確認） | **必須**（openapi_spec との矛盾・未定義エラー・境界条件の漏れをチェック） |
| domain_model | **自由設計**（RESTful インターフェースへ具体化。命名・構造はドメイン用語に従う） | — |
| api_description のみ | **自由設計**（ベストプラクティスに従い新規設計） | — |

> インターフェース制約と品質チェックは独立して扱う。openapi_spec の構造は変えないが、品質上の問題はフィードバックして修正を促す。

複合入力時の優先順位: `openapi_spec > implementation_code > domain_model > api_description`

#### openapi_spec の品質チェック項目（必須）

**境界条件**: パスパラメータの形式制約・クエリパラメータの範囲・リクエストボディの制約・ページネーション上限

**エラーポリシー**: 各 operation の 4xx/5xx の網羅性・operation 間のステータスコード一貫性・エラースキーマ統一・401/403 の使い分け

**矛盾**: パスパラメータの定義一致・required フィールドと example の整合・security 上書き漏れ・$ref 参照切れ

**曖昧さ**: description 省略・operation 間のフィールド名揺れ・nullable/optional の一貫性

**REST 原則**: パスの動詞使用・リソース複数形・メソッドの意味論的正確性・ステータスコード慣習・ネスト深さ
（→ 詳細チェック項目と警告フォーマット: [references/rest-design-guide.md](references/rest-design-guide.md)）

#### implementation_code との照合（openapi_spec + implementation_code が両方ある場合）

- 実装に存在するエンドポイントが openapi_spec に定義されているか（未文書エンドポイントの検出）
- 実装が返すエラーコード・ステータスが openapi_spec の responses と一致しているか
- 実装のバリデーションロジックと openapi_spec のスキーマ制約が一致しているか
- 実装の認証・認可の分岐が openapi_spec の security 定義に反映されているか

#### フィードバックテンプレート（必須）

```markdown
## OpenAPI 品質チェック結果

### 境界条件の欠落
| 対象 | 問題 | 推奨対応 |
|------|------|---------|

### エラーポリシーの問題
| 対象 | 問題 | 推奨対応 |
|------|------|---------|

### 矛盾
| 対象 | 問題 | 推奨対応 |
|------|------|---------|

### 曖昧さ・REST 原則違反
| 対象 | 問題 | 推奨対応 |
|------|------|---------|

上記の問題を修正してから OpenAPI を生成します。修正不要なものがあれば教えてください。
```

#### 制約分析テンプレート（必須）

```markdown
## 制約分析サマリー
- openapi_spec: あり / なし → [あり: 既存パス一覧 + 品質チェック結果]
- implementation_code: あり / なし → [あり: 変更コスト見積もり + 照合チェック結果]
- domain_model: あり / なし → [あり: RESTful 具体化の方針]

## 変更制約マップ
| 対象 | 変更可否 | 備考 |
|------|---------|------|
| （パス / スキーマ / 操作ごとに記載）| 維持 / 追加可 / 要承認 | |

## 設計方針サマリー
- 固定するインターフェース:
- 新規追加するインターフェース:
- 変更提案（ユーザー承認が必要なもの）:
- 品質改善（ユーザー確認後に反映）:
```

**Step 2 の出力**: `制約分析サマリー` + `OpenAPI 品質チェック結果`（openapi_spec がある場合）

---

### Step 3: エンドポイント設計

Step 2 の制約に従いエンドポイントを設計する。

#### REST 命名規約

```
GET    /users              # 一覧
POST   /users              # 作成
GET    /users/{id}         # 取得
PUT    /users/{id}         # 全体更新
PATCH  /users/{id}         # 部分更新
DELETE /users/{id}         # 削除

# ネストは 2 階層まで
GET /users/{userId}/orders/{orderId}

# アクションは動詞サブリソースで表現
POST /orders/{id}/cancel
```

#### REST 原則チェック（必須）

[references/rest-design-guide.md](references/rest-design-guide.md) のチェック表に従い全エンドポイントを検証する。
違反があれば同ファイルの警告フォーマットに従いユーザーに提示する。

#### バージョニング・互換性ポリシー

推奨: **URL パス方式**（`/v1/users`）

- **non-breaking**: 省略可能フィールド追加・新規エンドポイント追加
- **breaking**: 必須フィールド追加・フィールド削除/型変更・パス変更
- **deprecation**: 非推奨告知 → 併存期間明示（例: 90日）→ 利用状況モニタリング → 廃止

**Step 3 の出力**: `エンドポイント一覧（メソッド・パス・説明・認証）` + `互換性方針メモ`

---

### Step 4: リクエスト / レスポンス設計

詳細ガイド: [references/rest-design-guide.md](references/rest-design-guide.md)

#### 標準レスポンス構造

```json
// 成功（単一リソース）
{ "data": { "id": "123", "name": "Alice" }, "meta": { "requestId": "abc-xyz" } }

// 成功（コレクション）
{ "data": [...], "pagination": { "cursor": "...", "hasNext": true, "total": 500 } }
```

#### エラーレスポンス（RFC 7807 Problem Details）

```json
{
  "type": "https://api.example.com/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "The request body contains invalid fields.",
  "instance": "/users/register",
  "errors": [{ "field": "email", "message": "Invalid email format" }]
}
```

**Step 4 の出力**: `リクエスト/レスポンス仕様` + `エラーコード定義表`

---

### Step 5: OpenAPI スキーマ生成

[references/openapi-guide.md](references/openapi-guide.md) を読み込み、OpenAPI 3.0 YAML を生成する。

#### 生成品質要件（必須）

| 要件 | 内容 |
|------|------|
| description | 全 operation・全パスパラメータ・主要スキーマフィールドに記載（制約・認可条件・注意事項を含む） |
| example | 全スキーマにスキーマレベルの example を定義 |
| エラーレスポンス網羅 | 各 operation の発生しうる 4xx/5xx を全パターン列挙 |
| エラー examples | 401/403/404/422/429 にシナリオ別 examples（複数）を定義 |
| $ref 共通化 | 繰り返しスキーマは components で共通化 |

**Step 5 の出力**: `OpenAPI 3.0 YAML`（descriptions・examples・error responses を全て含む）

---

### Step 6: レビューと調整

#### REST 原則レビュー（必須）

生成した OpenAPI 全体を再確認する。新たな違反があれば警告を提示する。
openapi_spec 由来の既存違反には仕様書内（description または `x-rest-warning`）に注記を追記する。

#### 互換性レビュー（必須）

- breaking 変更の有無を判定する
- breaking なら、メジャーバージョン更新または deprecation 手順を適用する

#### セキュリティ最小チェック（必須）

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
| example | 全 schema に example が定義されているか |
| エラーレスポンス網羅 | 各 operation の発生しうる 4xx/5xx が列挙されているか |
| エラー examples | 401/403/404/422/429 にシナリオ別 example があるか |
| components 共通化 | 繰り返しスキーマが $ref で共通化されているか |

#### Step 2 品質チェック結果の再確認（openapi_spec がある場合）

Step 2 の未対応指摘があれば再度提示し、対応方針を確認する。

**Step 6 の出力**: `レビュー結果（REST 原則 / 互換性 / セキュリティ / ドキュメント品質）` + `修正アクション一覧`

---

## 成果物テンプレート

### 出力フォーマット（必須）

1. `収集した入力サマリー`
2. `制約分析サマリー`（入力種別ごとの制約と設計方針）
3. `OpenAPI 品質チェック結果`（openapi_spec がある場合。ユーザー確認後に次へ）
4. `エンドポイント一覧`（メソッド・パス・説明・認証）
5. `互換性ポリシー`（breaking/non-breaking/deprecation）
6. `OpenAPI 3.0 YAML`（解説・スキーマ例・エラーバリエーション含む）
7. `セキュリティ最小チェック結果`
8. `DoD 判定`
9. `次工程ハンドオフ`

### エンドポイント一覧表

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
- [ ] openapi_spec がある場合、品質チェック（境界条件・エラーポリシー・矛盾・曖昧さ・REST 原則）を実施しフィードバックした
- [ ] 制約マップに従いエンドポイントを設計した（openapi_spec の既存インターフェースは維持）
- [ ] 品質チェックの指摘事項をユーザーと確認し、対応方針が決まっている
- [ ] REST 原則の違反をチェックし、違反があればユーザーに警告した
- [ ] 既存 IF の REST 原則違反には仕様書内に注記を追記した
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

## 補助スクリプト

- **validate_openapi.py** — OpenAPI 3.x YAML / JSON ファイルのスキーマバリデーション

```bash
python scripts/validate_openapi.py openapi.yaml
python scripts/validate_openapi.py --strict openapi.yaml  # 警告も表示
python scripts/validate_openapi.py --json openapi.yaml    # CI 連携用
```

終了コード: 0 = 通過 / 1 = エラーあり / 2 = ファイル不在・パースエラー

---

## リファレンス

- [references/openapi-guide.md](references/openapi-guide.md) — OpenAPI 3.0 スニペット集（descriptions・examples・error responses テンプレート）
- [references/rest-design-guide.md](references/rest-design-guide.md) — REST 原則チェック表・HTTP ステータスコード・ページネーション・認証設計
