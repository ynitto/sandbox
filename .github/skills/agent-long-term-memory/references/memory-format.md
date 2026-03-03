# 記憶ファイルフォーマット仕様

## ディレクトリ構造

```
memories/
├── [カテゴリ名]/          # トピック・プロジェクト・課題種別など
│   ├── [記憶名].md
│   └── [記憶名].md
└── general/               # カテゴリ不明時のデフォルト
    └── [記憶名].md
```

- **カテゴリ名**: kebab-case（例: `auth-system`, `bug-investigation`, `architecture`）
- **記憶名**: kebab-case（例: `jwt-expiry-fix`, `api-design-decision`）

---

## ファイルフォーマット

```markdown
---
id: mem-YYYYMMDD-NNN
title: "記憶タイトル（一言で内容を表す）"
created: "YYYY-MM-DD"
updated: "YYYY-MM-DD"
status: active          # active | archived | deprecated
tags: [タグ1, タグ2]
related: []             # 関連する記憶ファイルのパス
summary: "この記憶の要点を1〜2文で。検索時の関連性判断に使う。"
---

# タイトル

## コンテキスト
どんな状況・課題に関する記憶か。プロジェクト名、背景など。

## 詳細
具体的な情報・調査結果・決定事項など。

## 学び・結論
次回に活かせる知見、注意点、推奨アクションなど。

## 次のステップ（任意）
作業を再開する場合にやること。
```

---

## フィールド定義

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `id` | ✓ | `mem-YYYYMMDD-NNN` 形式。NNNは同日連番 |
| `title` | ✓ | 記憶を一言で表すタイトル |
| `created` | ✓ | 作成日（ISO 8601） |
| `updated` | ✓ | 最終更新日 |
| `status` | ✓ | `active`（有効）/ `archived`（古い）/ `deprecated`（廃止） |
| `tags` | - | 検索用タグ（配列） |
| `related` | - | 関連記憶ファイルのパス（配列） |
| `summary` | ✓ | 関連性判断用の要約（1〜2文） |

---

## 良いsummaryの例

```
# 悪い例（情報不足）
summary: "認証について調べた"

# 良い例（判断に必要な情報を含む）
summary: "JWTトークンの有効期限を15分に設定した理由と、リフレッシュトークン実装のポイント。auth-serviceに影響。"
```

## カテゴリ命名ガイドライン

| 用途 | カテゴリ例 |
|------|-----------|
| 機能・モジュール | `auth`, `payment`, `notifications` |
| バグ調査 | `bug-investigation`, `incident-YYYY-MM` |
| アーキテクチャ決定 | `architecture`, `adr` |
| 調査・リサーチ | `research`, `spike` |
| 汎用 | `general` |
