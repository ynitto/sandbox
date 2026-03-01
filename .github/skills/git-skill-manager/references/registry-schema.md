# レジストリスキーマ

パス: `~/.copilot/skill-registry.json`（Windows: `%USERPROFILE%\.copilot\skill-registry.json`）

## 目次

- [完全なJSON例](#完全なjson例)
- [フィールド説明](#フィールド説明)
- [マイグレーション](#マイグレーション)

## 完全なJSON例

```json
{
  "version": 5,
  "repositories": [
    {
      "name": "team-skills",
      "url": "https://github.com/myorg/agent-skills.git",
      "branch": "main",
      "skill_root": "skills",
      "description": "チーム共有スキル集",
      "readonly": false,
      "priority": 1
    }
  ],
  "installed_skills": [
    {
      "name": "docx-converter",
      "source_repo": "team-skills",
      "source_path": "skills/docx-converter",
      "commit_hash": "a1b2c3d",
      "installed_at": "2026-02-14T12:00:00Z",
      "enabled": true,
      "pinned_commit": null,
      "feedback_history": [
        {
          "timestamp": "2026-02-15T10:00:00Z",
          "verdict": "needs-improvement",
          "note": "PDF変換時に文字化けが発生した",
          "refined": false
        }
      ],
      "pending_refinement": true,
      "version": null,
      "central_version": null,
      "version_ahead": false,
      "lineage": {
        "origin_repo": "team-skills",
        "origin_commit": "a1b2c3d",
        "origin_version": null,
        "local_modified": false,
        "diverged_at": null,
        "local_changes_summary": ""
      },
      "metrics": {
        "total_executions": 12,
        "ok_rate": 0.75,
        "last_executed_at": "2026-02-20T10:00:00Z",
        "central_ok_rate": null
      }
    }
  ],
  "core_skills": ["scrum-master", "git-skill-manager", "skill-creator", "requirements-definer", "skill-recruiter", "skill-evaluator", "generating-skills-from-copilot-logs", "sprint-reviewer", "codebase-to-skill"],
  "remote_index": {
    "team-skills": {
      "updated_at": "2026-02-15T10:00:00Z",
      "skills": [
        {"name": "docx-converter", "description": "Word文書をPDFに変換する..."},
        {"name": "image-resizer", "description": "画像をリサイズする..."}
      ]
    }
  },
  "profiles": {
    "default": ["*"],
    "frontend": ["react-guide", "css-linter", "storybook"],
    "backend": ["api-guide", "db-migration", "auth"]
  },
  "active_profile": null,
  "auto_update": {
    "enabled": false,
    "interval_hours": 24,
    "notify_only": true,
    "last_checked_at": "2026-02-20T09:00:00+00:00"
  },
  "node": {
    "id": "node-abc12345",
    "name": "my-machine",
    "created_at": "2026-02-01T00:00:00Z"
  },
  "promotion_policy": {
    "min_ok_count": 3,
    "max_problem_rate": 0.2,
    "require_local_modified": false,
    "auto_pr": false,
    "notify_on_eligible": true
  },
  "sync_policy": {
    "auto_accept_patch": true,
    "auto_accept_minor": false,
    "protect_local_modified": true
  },
  "contribution_queue": []
}
```

## フィールド説明

**repositories[].priority** (整数、デフォルト: 100):
- 値が小さいほど優先度が高い
- 同名スキルの競合時、サブエージェント経由（非対話）では優先度の高いリポジトリを自動採用する
- ユーザー直接呼び出しでは対話的に選択を求める

**installed_skills[].enabled** (真偽値、デフォルト: true):
- false のスキルは `discover_skills.py` によるメタデータ収集から除外される
- ディスク上にはスキルが残るため、再有効化は即座に完了する

**installed_skills[].pinned_commit** (文字列 or null、デフォルト: null):
- null の場合、pull 時に常に最新（HEAD）を取得する
- コミットハッシュが設定されている場合、pull 時にそのコミットを checkout して取得する
- `pin` 操作で現在の commit_hash に固定、`unpin` で解除
- `lock` で全スキルを一括 pin、`unlock` で全スキルを一括 unpin

**installed_skills[].source_repo** (文字列):
- `"workspace"`: ワークスペースのスキルディレクトリ（`<workspace-skill-dir>`）に置かれた試用中スキル（チャット経由で作成）
- `"local"`: `promote` 操作でユーザー領域に昇格済みのスキル
- その他: リポジトリ名（`pull` でインストールしたスキル）
- `"workspace"` のスキルは `skill-evaluator` の評価対象になる

**installed_skills[].feedback_history** (配列、デフォルト: []):
- スキル使用後にユーザーが提供したフィードバックの履歴
- 各エントリ: `timestamp`（ISO 8601）、`verdict`（ok/needs-improvement/broken）、`note`（コメント）、`refined`（改良済みフラグ）
- `record_feedback.py` で記録し、`refine` 操作の入力として使われる

**installed_skills[].pending_refinement** (真偽値、デフォルト: false):
- `needs-improvement` または `broken` の未対応フィードバックが存在する場合 true
- `discover_skills.py` がこの値を参照してスキルのソート順を決定する（改良待ちは後ろへ）
- `refine` 操作完了後に false に戻る

**installed_skills[].version** (文字列 or null、デフォルト: null):
- スキルのセマンティックバージョン（例: `"1.2.0"`）。SKILL.md のフロントマターから読み取る。未記載の場合は null
- `central_version` と比較してローカルが先行しているか判定する

**installed_skills[].central_version** (文字列 or null、デフォルト: null):
- リモートリポジトリの最新バージョン。pull 時に取得

**installed_skills[].version_ahead** (真偽値、デフォルト: false):
- ローカルバージョンがリモートを上回っている場合 true

**installed_skills[].lineage** (オブジェクト):
- スキルのソース追跡情報
- `origin_repo`: pull 元リポジトリ名
- `origin_commit`: pull 時のコミットハッシュ
- `origin_version`: pull 時のリモートバージョン
- `local_modified`: ローカルでファイルを編集した場合 true（`delta_tracker.py` が更新）
- `diverged_at`: ローカル変更が最初に検出された日時（ISO 8601）
- `local_changes_summary`: ローカル変更の要約テキスト

**installed_skills[].metrics** (オブジェクト):
- スキルの実行統計（`record_feedback.py` が更新）
- `total_executions`: 総実行回数
- `ok_rate`: ok 判定の割合（0.0〜1.0）。データなしは null
- `last_executed_at`: 最後の実行日時（ISO 8601）。未実行は null
- `central_ok_rate`: リモートリポジトリ全体の ok 率。null は未取得

**node** (オブジェクト、v5):
- このマシン固有のノード識別情報（`node_identity.py` が管理）
- `id`: ランダム生成のユニーク ID（例: "node-a1b2c3d4"）
- `name`: ノードの識別名（デフォルトはホスト名）
- `created_at`: ノード ID 生成日時（ISO 8601）

**promotion_policy** (オブジェクト、v5):
- ワークスペーススキルの昇格推奨判定ポリシー（`promotion_policy.py` が参照）
- `min_ok_count` (整数、デフォルト: 3): 昇格に必要な ok フィードバック数
- `max_problem_rate` (実数、デフォルト: 0.2): 許容する問題率の上限
- `require_local_modified` (真偽値、デフォルト: false): true にするとローカル改善がないスキルを除外
- `auto_pr` (真偽値、デフォルト: false): 昇格推奨時に自動 PR を作成するか
- `notify_on_eligible` (真偽値、デフォルト: true): 昇格条件を満たしたときに通知するか

**sync_policy** (オブジェクト、v5):
- 自動更新・pull 時の動作制御設定
- `auto_accept_patch` (真偽値、デフォルト: true): パッチバージョンアップ（X.Y.Z1→X.Y.Z2）を自動 pull するか。`notify_only=false` 時のみ有効
- `auto_accept_minor` (真偽値、デフォルト: false): マイナーバージョンアップ（X.Y1→X.Y2）を自動 pull するか。`notify_only=false` 時のみ有効。メジャーアップは常に手動確認
- `protect_local_modified` (真偽値、デフォルト: true): true の場合、ローカル改善済みスキルを自動 pull で上書きしない

**contribution_queue** (配列、v5):
- ローカル改善をリモートに貢献するための待ちキュー（`delta_tracker.py` が管理）
- 各エントリ: `skill_name`、`node_id`、`queued_at`、`status`（pending/in-progress/done）

**core_skills** (文字列リスト):
- 使用頻度に関わらず常に最優先でロードされるスキル名のリスト
- scrum-master、git-skill-manager とその依存スキル（skill-creator / requirements-definer / skill-recruiter / skill-evaluator / generating-skills-from-copilot-logs / sprint-reviewer / codebase-to-skill）を登録する
- `discover_skills.py` のソート時にこのリストのスキルを先頭に配置する

**remote_index** (オブジェクト):
- リポジトリ名 → スキル一覧のキャッシュ。`search` がこのインデックスを参照するため、ネットワーク不要で高速に検索できる
- `pull` 実行時に自動更新される
- `search --refresh` で明示的にリモートから更新できる
- `updated_at` で鮮度を確認可能

**profiles** (オブジェクト):
- プロファイル名 → スキル名のリスト。`"*"` は「全スキル」を意味する
- `active_profile` が null の場合、個別の enabled フラグに従う
- `active_profile` が設定されている場合、プロファイル内のスキルのみ enabled として扱う

**auto_update** (オブジェクト):
- スキルの自動更新チェック設定
- `enabled` (真偽値、デフォルト: false): 自動更新チェックの有効/無効
- `interval_hours` (整数、デフォルト: 24): チェック間隔（時間単位、最小: 1）
- `notify_only` (真偽値、デフォルト: true): true の場合は更新通知のみ。false の場合は自動で pull を実行する
- `last_checked_at` (文字列 or null): 最後にチェックした日時（ISO 8601）。null は未チェック

## マイグレーション

古いバージョンのレジストリを読み込んだ場合、`migrate_registry(reg)` が自動でフィールドを追加する。

| 旧 ver → 新 ver | 追加されるフィールド |
|----------------|-----------------|
| v1 → v2 | `enabled`、`pinned_commit`、`core_skills`、`profiles`、`remote_index` |
| v2 → v3 | `feedback_history`、`pending_refinement` |
| v3 → v4 | `auto_update` |
| v4 → v5 | `version`、`central_version`、`version_ahead`、`lineage`、`metrics`、`node`、`promotion_policy`、`sync_policy`、`contribution_queue` |

→ 実装: `scripts/registry.py` — `migrate_registry(reg)`
