# Deprecation ライフサイクル

スキルの非推奨化・アーカイブ・削除の手順と基準を定義する。

## 目次

- [ライフサイクル概要](#ライフサイクル概要)
- [各ステージの定義](#各ステージの定義)
- [フロントマター変更例](#フロントマター変更例)
- [Registry への反映](#registry-への反映)
- [スキル利用時の警告表示](#スキル利用時の警告表示)
- [強制非推奨化の条件](#強制非推奨化の条件)

---

## ライフサイクル概要

```
Active ──→ Deprecated ──→ Archived ──→ Removed
           (2スプリント通知)  (参照専用)    (完全削除)
```

| ステージ | tier 値 | 説明 |
|---------|---------|------|
| Active | `core` / `stable` / `experimental` / `draft` | 通常利用可能 |
| Deprecated | `deprecated` | 非推奨。代替スキルへの移行を推奨。引き続き動作するが新規利用は避ける |
| Archived | — | `.github/skills/_archived/` に移動。参照のみ可。新規利用不可 |
| Removed | — | ディレクトリ削除。CHANGELOG にのみ記録 |

---

## 各ステージの定義

### Active → Deprecated

**実行条件（いずれか）:**
- 代替スキルが stable 以上に昇格した
- スキルの機能が他スキルに統合された
- 2 スプリント以上 ok フィードバックがなく broken が 2 件以上
- 明示的なリネーム（例: `security-auditor` → `security-reviewer`）

**手順:**
1. `git-skill-manager deprecate <skill-name> --replaced-by <new-skill>` を実行
2. SKILL.md フロントマターを更新（[フロントマター変更例](#フロントマター変更例) 参照）
3. skill-registry.json を更新
4. 非推奨化をチームに通知

**通知期間:** 2 スプリント（最低 2 週間）

### Deprecated → Archived

**実行条件（すべて）:**
- `deprecated_since` から 2 スプリント以上経過
- 代替スキルの使用率が移行前の旧スキル使用率と同等以上
- 旧スキルへの依存参照がゼロ（`cross_reference_check.py` で確認）

**手順:**
1. `git-skill-manager archive <skill-name>` を実行
2. `.github/skills/_archived/<skill-name>/` へ移動
3. skill-registry.json の `installed_skills` から削除、`archived_skills` に追記

### Archived → Removed

**実行条件（すべて）:**
- アーカイブから 4 スプリント以上経過
- 参照・復元の要望がゼロ
- チームレビューで削除承認済み

**手順:**
1. `_archived/<skill-name>/` ディレクトリを削除
2. CHANGELOG.md に `Removed: <skill-name> (was deprecated since <version>)` を追記

---

## フロントマター変更例

### Deprecated スキルの SKILL.md

```yaml
---
name: security-auditor
description: >-
  【非推奨】security-reviewer を使用してください。セキュリティ審査スキル（deprecated）。
metadata:
  version: 1.2.3
  tier: deprecated
  deprecated_by: security-reviewer
  deprecated_since: "1.2.3"
  category: review
  tags:
    - security
    - deprecated
---
```

### フロントマターの必須フィールド（Deprecated 時）

| フィールド | 型 | 説明 |
|------------|-----|------|
| `tier` | `"deprecated"` | 必須 |
| `deprecated_by` | string | 代替スキル名（必須） |
| `deprecated_since` | string | 非推奨化したバージョン番号（必須） |

---

## Registry への反映

`skill-registry.json` の `deprecated_skills` リストに追記する:

```json
{
  "deprecated_skills": [
    {
      "name": "security-auditor",
      "deprecated_by": "security-reviewer",
      "deprecated_since": "1.2.3",
      "archived_at": null
    }
  ]
}
```

---

## スキル利用時の警告表示

`skill-selector` および `scrum-master` は、deprecated スキルが選択された場合に以下を表示する:

```
⚠️ [非推奨] security-auditor は deprecated です。
   代わりに security-reviewer を使用してください。
   このスキルは <version> で削除予定です。
```

`skill-evaluator` の `quality_check.py` は `tier: deprecated` かつ `deprecated_by` 未設定の場合に `DEPRECATED_NO_SUCCESSOR` (WARN) を報告する。

---

## 強制非推奨化の条件

以下の条件をすべて満たすスキルは `skill-evaluator` が自動的に `DEPRECATION_CANDIDATE` (INFO) を報告する。実際の非推奨化は人間が判断する:

- 最終 ok フィードバックから 6 スプリント以上経過
- broken フィードバックが 3 件以上
- 代替として機能的に重複するスキルが stable 以上で存在する
