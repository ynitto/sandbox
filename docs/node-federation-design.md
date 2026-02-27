# ノードフェデレーション設計

各ノードでローカル改善を行いつつ、必要なものだけを中央リポジトリへ集約する仕組みの設計。

---

## 現状の課題と設計方針

### 現状フロー

```
中央リポジトリ
   ↓ pull（全スキルをそのまま取得）
ノード（~/.copilot/skills/）
   ↓ フィードバック記録（ok/needs-improvement/broken）
   ↓ 手動 promote/push（判断基準が曖昧）
中央リポジトリ
```

### 目指すフロー

```
中央リポジトリ（信頼された知識の集積）
   ↓ pull（バージョン付き、選択的）
ノード（ローカルで改善・試用・評価）
   ├─ ローカル改善（中央との差分を追跡）
   ├─ 自動評価（ポリシーに基づく昇格判定）
   └─ 選択的貢献（基準を満たしたスキルのみ push）
中央リポジトリ（ノード貢献のレビュー・統合）
```

---

## 設計コンポーネント

### 1. ノードアイデンティティ（`node_identity.py`）

各ノードに一意のIDを付与し、「誰の改善か」を追跡可能にする。

**レジストリへの追加フィールド（v5）:**

```json
{
  "node": {
    "id": "node-abc123",
    "name": "tokyo-team-dev",
    "created_at": "2026-02-27T00:00:00Z"
  }
}
```

**用途:**
- push 時のコミットメッセージに node-id を付与
- 中央での「どのノード由来か」のトレーサビリティ確保

---

### 2. スキル系譜追跡（lineage）

ノードが中央スキルを改善した場合、元バージョンとの関係を記録する。

**レジストリ `installed_skills[]` への追加フィールド:**

```json
{
  "name": "react-frontend-coder",
  "lineage": {
    "origin_repo": "team-skills",
    "origin_commit": "a1b2c3d",
    "origin_version": "1.2.0",
    "local_modified": true,
    "diverged_at": "2026-02-20T00:00:00Z",
    "local_changes_summary": "RSC対応を追加"
  }
}
```

**delta_tracker.py** が担当:
- `git diff` 相当の変更検出（SKILL.md の内容比較）
- 中央バージョンとのハッシュ比較
- `local_modified` フラグの自動更新

---

### 3. セマンティックバージョニング

SKILL.md フロントマターへの `version` フィールド追加。

**SKILL.md の変更:**

```yaml
---
name: react-frontend-coder
description: "..."
version: 1.3.0
min_skill_framework: "5.0"
---
```

**レジストリへの追加フィールド:**

```json
{
  "name": "react-frontend-coder",
  "version": "1.3.0",
  "central_version": "1.2.0",
  "version_ahead": true
}
```

`version_ahead: true` = ノードが中央より進んでいる → 貢献候補

---

### 4. 昇格ポリシーエンジン（`promotion_policy.py`）

「中央にあげる基準」を設定ファイルで定義可能にする。

**レジストリへの追加フィールド:**

```json
{
  "promotion_policy": {
    "min_ok_count": 3,
    "max_problem_rate": 0.1,
    "require_local_modified": true,
    "auto_pr": false,
    "notify_on_eligible": true
  }
}
```

**判定ロジック:**

```
昇格条件（AND）:
  ✓ ok_count >= min_ok_count
  ✓ problem_count / total <= max_problem_rate
  ✓ local_modified == true（ローカルで何か改善している）
  ✓ version_ahead == true（中央より新しいバージョン）

昇格除外条件（OR）:
  ✗ pending_refinement == true（未解決の問題がある）
  ✗ pinned_commit が設定済み（意図的にバージョン固定）
```

---

### 5. 貢献キュー（contribution queue）

push 前に「貢献候補」をキューに積み、レビューを経て中央へ送る。

**レジストリへの追加フィールド:**

```json
{
  "contribution_queue": [
    {
      "skill_name": "react-frontend-coder",
      "queued_at": "2026-02-27T10:00:00Z",
      "reason": "ok:5件, version_ahead: 1.2.0→1.3.0",
      "status": "pending_review",
      "node_id": "node-abc123"
    }
  ]
}
```

**ステータス遷移:**

```
eligible（昇格条件を満たした）
  → queued（ユーザーがキューに追加）
  → pending_review（PR作成済み）
  → merged（中央に取り込まれた）
  → rejected（中央に却下された）
```

---

### 6. 選択的同期ポリシー（中央→ノード方向）

pull 時に「何を取り込むか」を制御する。

**レジストリへの追加フィールド:**

```json
{
  "sync_policy": {
    "auto_accept_patch": true,
    "auto_accept_minor": false,
    "protect_local_modified": true,
    "max_version_jump": "minor"
  }
}
```

**`protect_local_modified: true` の動作:**
- ローカル改善済みスキル（`local_modified: true`）は中央の更新で上書きしない
- 代わりに「中央が更新されましたが、あなたのローカル版は保護されています」と通知

---

### 7. 実行メトリクス

フィードバックの verdict だけでなく、定量的な効果測定を追加。

**レジストリ `installed_skills[]` への追加フィールド:**

```json
{
  "name": "react-frontend-coder",
  "metrics": {
    "total_executions": 15,
    "ok_rate": 0.87,
    "avg_feedback_note_length": 42,
    "last_executed_at": "2026-02-27T09:00:00Z",
    "central_ok_rate": 0.72
  }
}
```

`ok_rate > central_ok_rate` → ノード改善が効果的である証拠 → 昇格の根拠

---

## コンポーネント関係図

```
┌─────────────────────────────────────────────────────────┐
│                    中央リポジトリ                         │
│   スキル v1.2.0 ────────────────────────────────────→   │
│                                                         │
│   ← PR: "react-frontend-coder v1.3.0 from node-abc123" │
└────────────────────────┬────────────────────────────────┘
                         │ pull（sync_policy に従う）
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  ノード（~/.copilot/）                    │
│                                                         │
│  node_identity.py                                       │
│    └─ node-id: "node-abc123"                            │
│                                                         │
│  delta_tracker.py                                       │
│    └─ local_modified: true                              │
│    └─ origin_commit: "a1b2c3d" → 現在: "独自改善版"      │
│                                                         │
│  record_feedback.py                                     │
│    └─ ok×5, needs-improvement×0                        │
│    └─ metrics.ok_rate: 0.93                             │
│                                                         │
│  promotion_policy.py                                    │
│    └─ 条件評価: eligible!                                │
│    └─ contribution_queue に追加                         │
│                                                         │
│  push.py（既存）                                        │
│    └─ PR作成: add-skill/react-frontend-coder            │
└─────────────────────────────────────────────────────────┘
```

---

## レジストリスキーマ v5 全体像

```json
{
  "version": 5,
  "node": {
    "id": "node-abc123",
    "name": "optional-human-readable-name",
    "created_at": "2026-02-27T00:00:00Z"
  },
  "promotion_policy": {
    "min_ok_count": 3,
    "max_problem_rate": 0.1,
    "require_local_modified": true,
    "auto_pr": false,
    "notify_on_eligible": true
  },
  "sync_policy": {
    "auto_accept_patch": true,
    "auto_accept_minor": false,
    "protect_local_modified": true,
    "max_version_jump": "minor"
  },
  "contribution_queue": [],
  "repositories": [ "...既存..." ],
  "installed_skills": [
    {
      "...既存フィールド...",
      "version": "1.3.0",
      "central_version": "1.2.0",
      "version_ahead": true,
      "lineage": {
        "origin_repo": "team-skills",
        "origin_commit": "a1b2c3d",
        "origin_version": "1.2.0",
        "local_modified": true,
        "diverged_at": "2026-02-20T00:00:00Z",
        "local_changes_summary": ""
      },
      "metrics": {
        "total_executions": 0,
        "ok_rate": null,
        "last_executed_at": null,
        "central_ok_rate": null
      }
    }
  ]
}
```

---

## 実装優先順位

| フェーズ | 対象 | 効果 |
|---------|------|------|
| Phase 1 | `registry.py` v5 マイグレーション | 全機能の基盤 |
| Phase 1 | `node_identity.py` | ノードID付与 |
| Phase 1 | `promotion_policy.py` | 昇格基準の自動判定 |
| Phase 2 | `delta_tracker.py` | ローカル変更の検出 |
| Phase 2 | `sync_policy` を `pull.py` に適用 | 上書き保護 |
| Phase 3 | メトリクス収集を `record_feedback.py` に追加 | 定量的根拠 |
| Phase 3 | 貢献キューUIを `manage.py` に追加 | エンドツーエンド |
