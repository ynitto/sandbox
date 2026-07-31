# インベントリ抽出ガイド

Phase 2 でコードインベントリを抽出する際の言語別ガイド。抽出単位はトレーサビリティの基盤となる。

## 目次

- [インベントリJSONフォーマット](#インベントリjsonフォーマット)
- [言語別抽出コマンド](#言語別抽出コマンド)
- [優先度付け](#優先度付け)
- [ドメインモデルとの連携](#ドメインモデルとの連携)

---

## インベントリJSONフォーマット

```json
{
  "language": "Python",
  "framework": "FastAPI",
  "units": [
    {
      "id": "INV-001",
      "type": "endpoint",
      "name": "GET /users/{id}",
      "file": "src/routers/users.py",
      "lines": "42-58",
      "description": "ユーザー情報取得エンドポイント",
      "covered_in_chapter": null
    }
  ]
}
```

`covered_in_chapter` は Phase 4 の検証時に章番号で埋める（`null` = 未カバー）。

---

## 言語別抽出コマンド

### Python

```bash
# クラス一覧
grep -rn "^class " --include="*.py" . | grep -v "test_"

# 関数一覧（トップレベル）
grep -rn "^def " --include="*.py" . | grep -v "test_"

# FastAPI/Flask エンドポイント
grep -rn "@app\.\|@router\." --include="*.py" .

# Django URL設定
grep -rn "path\(\|url\(" --include="urls.py" -r .
```

### TypeScript / JavaScript

```bash
# クラス一覧
grep -rn "^export class\|^class " --include="*.ts" --include="*.tsx" .

# 関数エクスポート
grep -rn "^export function\|^export const\|^export async function" \
  --include="*.ts" --include="*.tsx" .

# Express ルート
grep -rn "router\.\|app\.\(get\|post\|put\|delete\|patch\)" \
  --include="*.ts" --include="*.js" .

# Next.js ページ/APIルート
find . -path "*/pages/**/*.tsx" -o -path "*/app/**/*.tsx" \
  -o -path "*/pages/api/**" | grep -v node_modules
```

### Java / Kotlin

```bash
# クラス一覧
grep -rn "^public class\|^class " --include="*.java" --include="*.kt" .

# Spring Bootエンドポイント
grep -rn "@GetMapping\|@PostMapping\|@PutMapping\|@DeleteMapping\|@RequestMapping" \
  --include="*.java" --include="*.kt" .

# エンティティ
grep -rn "@Entity\|@Table" --include="*.java" --include="*.kt" .
```

### Go

```bash
# 構造体一覧
grep -rn "^type .* struct" --include="*.go" .

# パブリック関数
grep -rn "^func [A-Z]" --include="*.go" .

# HTTPハンドラ
grep -rn "http\.HandleFunc\|r\.HandleFunc\|router\." --include="*.go" .
```

### PHP

```bash
# クラス一覧
grep -rn "^class " --include="*.php" .

# Laravelルート
grep -rn "Route::" --include="*.php" routes/

# トレイト
grep -rn "^trait " --include="*.php" .
```

---

## 優先度付け

抽出したインベントリは以下の基準で優先度を付ける:

| 優先度 | 基準 | 例 |
|---|---|---|
| P1: 必須 | エンドポイント、パブリックAPI、エンティティ | `/api/users`, `User` クラス |
| P2: 重要 | ビジネスロジック、サービス層 | `OrderService`, `calculateTax()` |
| P3: 任意 | ユーティリティ、ヘルパー | `formatDate()`, `logger.py` |

粒度が「概要」の場合は P1 のみ、「中粒度」は P1+P2、「詳細」は全項目を対象とする。

---

## ドメインモデルとの連携

`domain-modeler` スキルがある場合、インベントリ抽出後に以下を実行する:

```
domain-modeler スキルの逆引きモードで、以下のファイル群からドメインモデルを抽出してください:
[inventory.json の units から file を列挙]
```

抽出されたドメインモデル（エンティティ・集約・値オブジェクト）を `inventory.json` に統合する。
