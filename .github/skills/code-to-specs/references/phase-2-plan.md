# Phase 2: 計画とWBS分割

**目的**: 仕様書の骨組みを確定し、Phase 3 の調査タスクを設計する。

## 目次

- [2-1. コードインベントリの抽出](#2-1-コードインベントリの抽出)
- [2-2. 章構成の決定（WBS）](#2-2-章構成の決定wbs)
- [2-3. WBSの保存](#2-3-wbsの保存)
- [2-4. WBSの確認](#2-4-wbsの確認)
- [完了条件](#完了条件)

---

## 手順

### 2-1. コードインベントリの抽出

言語・FWに応じてコードの「抽出単位」をリストアップする。言語別のコマンドは `inventory-guide.md` を参照。

抽出単位の優先度（goal.json の粒度設定に従う）:

| 粒度 | 対象 |
|---|---|
| 概要 | P1のみ（エンドポイント、公開API、エンティティ） |
| 中粒度 | P1 + P2（サービス層、ビジネスロジック） |
| 詳細 | 全項目（ユーティリティ、ヘルパー含む） |

`.specs-work/inventory.json` に保存する:

```json
{
  "language": "Python",
  "framework": "FastAPI",
  "extracted_at": "2025-01-01T00:00:00Z",
  "units": [
    {
      "id": "INV-001",
      "type": "endpoint",
      "name": "GET /users/{id}",
      "file": "src/routers/users.py",
      "lines": "42-58",
      "priority": "P1",
      "covered_in_chapter": null
    }
  ]
}
```

`covered_in_chapter` は Phase 4 で埋める（`null` = 未カバー）。

### 2-2. 章構成の決定（WBS）

**命名規約（厳守）**: `NN-slug.md`
- `NN`: 2桁ゼロ埋め（00〜99）
- `slug`: ASCII小文字・数字・ハイフンのみ
- 例: `01-overview.md`, `05-authentication.md`

**予約ファイル（必須）**:
- `00-metadata.md` — Phase 6 で充填
- `99-unresolved.md` — Phase 6 で充填
- `traceability.md` — Phase 6 で生成

章構成はテンプレートを参照しつつ、コードベースの実態に合わせて調整する（`templates.md` 参照）。

### 2-3. WBSの保存

`.specs-work/wbs.json` に保存する:

```json
{
  "template": "APIサービス仕様書",
  "chapters": [
    {
      "id": "CH-00",
      "file": "00-metadata.md",
      "title": "メタデータ",
      "status": "reserved",
      "inventory_ids": []
    },
    {
      "id": "CH-01",
      "file": "01-overview.md",
      "title": "サービス概要",
      "status": "pending",
      "inventory_ids": ["INV-001", "INV-002"]
    }
  ]
}
```

`status` の値: `reserved`（Phase 6で充填）/ `pending`（未調査）/ `in-progress`/ `done`

### 2-4. WBSの確認

ユーザーに章構成の一覧を提示し、追加・変更・削除の希望を確認する:

```
Phase 2 完了。以下の章構成で進めます:

  00-metadata.md        — メタデータ（自動生成）
  01-overview.md        — サービス概要
  02-architecture.md    — アーキテクチャ
  03-authentication.md  — 認証・認可
  04-endpoints.md       — エンドポイント一覧
  ...
  99-unresolved.md      — 未確定事項（自動生成）
  traceability.md       — トレーサビリティ表（自動生成）

修正があれば教えてください。問題なければ Phase 3（調査）を開始します。
```

---

## 完了条件

- [ ] `.specs-work/inventory.json` が保存されている
- [ ] `.specs-work/wbs.json` が保存されている
- [ ] ユーザーが章構成を承認している
- [ ] `state.json` の `chapterCount` を更新する
