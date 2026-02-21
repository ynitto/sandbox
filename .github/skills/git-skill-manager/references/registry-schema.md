# レジストリスキーマ

パス: `~/.copilot/skill-registry.json`（Windows: `%USERPROFILE%\.copilot\skill-registry.json`）

## 完全なJSON例

```json
{
  "version": 4,
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
      "pending_refinement": true
    }
  ],
  "core_skills": ["scrum-master", "git-skill-manager", "skill-creator", "sprint-reviewer", "codebase-to-skill"],
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
  }
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
- `"workspace"`: `.github/skills/` に置かれた試用中スキル（チャット経由で作成）
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

**core_skills** (文字列リスト):
- 使用頻度に関わらず常に最優先でロードされるスキル名のリスト
- scrum-master、git-skill-manager、skill-creator など基盤スキルを登録する
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

version: 1〜3 のレジストリを読み込んだ場合、新フィールドにデフォルト値を設定して自動マイグレーションする。

→ 実装: `scripts/registry.py` — `migrate_registry(reg)`
