# ltm-use 設定リファレンス

## 目次

- [git リポジトリ設定](#git-リポジトリ設定copilotskill-registryjson)
- [メモリー設定](#メモリー設定copilotmemoryconfijson)
- [デフォルト動作](#デフォルト動作設定ファイル未作成時)
- [Windows 環境の注意事項](#windows-環境の注意事項)

---

## git リポジトリ設定（`~/.copilot/skill-registry.json`）

git-skill-manager と共通のリポジトリ設定を使用する。

### 設定例

```json
{
  "repositories": [
    {
      "name": "origin",
      "url": "git@github.com:org/agent-skills.git",
      "branch": "main",
      "readonly": false,
      "priority": 1,
      "memory_root": "memories"
    },
    {
      "name": "team-b",
      "url": "git@github.com:team-b/skills.git",
      "branch": "main",
      "readonly": true,
      "priority": 2
    }
  ]
}
```

### フィールド説明

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | ✓ | リポジトリ識別名（英数字・ハイフン） |
| `url` | ✓ | Git リポジトリ URL（SSH / HTTPS） |
| `branch` | ✓ | ブランチ名（デフォルト: `main`） |
| `readonly` | ✓ | 読み取り専用フラグ（`true`: pull のみ、`false`: push 可能） |
| `priority` | ✓ | 優先度（数値が小さいほど優先、書き込みは最優先リポジトリへ） |
| `memory_root` | - | shared 記憶の保存先（省略時: `"memories"`、リポジトリルート配下のディレクトリ名） |

### 動作

- **複数リポジトリ**: `priority` 順に処理、書き込みは最優先（最小値）リポジトリへ
- **readonly**: `true` のリポジトリは pull のみ、commit/push 不可
- **フォールバック**: `skill-registry.json` が未設定の場合、`config.json` の `shared_remote` を使用

### ローカルディレクトリ

各リポジトリは `~/.copilot/memory/shared/<name>/` にクローンされる。

```
~/.copilot/memory/shared/
├── origin/
│   └── memories/           ← memory_root
│       ├── auth/
│       └── architecture/
└── team-b/
    └── memories/
```

---

## メモリー設定（`~/.copilot/memory/config.json`）

### 設定例

```json
{
  "shared_remote": "git@github.com:org/shared-memories.git",
  "shared_branch": "main",
  "auto_promote_threshold": 85,
  "semi_auto_promote_threshold": 70,
  "cleanup_inactive_days": 30,
  "cleanup_archived_days": 60
}
```

### フィールド説明

| フィールド | デフォルト | 説明 |
|-----------|-----------|------|
| `shared_remote` | - | フォールバック用 Git リポジトリ URL（skill-registry.json 未設定時） |
| `shared_branch` | `"main"` | フォールバック用ブランチ名 |
| `auto_promote_threshold` | `85` | 自動昇格の閾値（share_score >= この値で自動昇格） |
| `semi_auto_promote_threshold` | `70` | 半自動昇格の閾値（この値以上で昇格候補として表示） |
| `cleanup_inactive_days` | `30` | 未アクセス記憶の削除閾値（日数） |
| `cleanup_archived_days` | `60` | アーカイブ記憶の削除閾値（日数） |

### 昇格閾値のカスタマイズ

```json
{
  "auto_promote_threshold": 90,        // より厳格な自動昇格
  "semi_auto_promote_threshold": 80    // 昇格候補の表示閾値を上げる
}
```

### クリーンアップ閾値のカスタマイズ

```json
{
  "cleanup_inactive_days": 60,    // 未アクセス記憶を60日後に削除候補に
  "cleanup_archived_days": 90     // アーカイブ記憶を90日後に削除候補に
}
```

---

## デフォルト動作（設定ファイル未作成時）

`~/.copilot/memory/config.json` が存在しない場合、以下のデフォルト値で動作する:

```json
{
  "auto_promote_threshold": 85,
  "semi_auto_promote_threshold": 70,
  "cleanup_inactive_days": 30,
  "cleanup_archived_days": 60
}
```

`skill-registry.json` が未設定の場合、shared 記憶の管理はスキップされる（workspace / home のみ動作）。

---

## Windows 環境の注意事項

### パス表記

- `~` は `%USERPROFILE%` に読み替える
- 例: `~/.copilot/memory/` → `%USERPROFILE%\.copilot\memory\`

### 設定ファイル配置場所

| ファイル | パス |
|---------|------|
| `config.json` | `%USERPROFILE%\.copilot\memory\config.json` |
| `skill-registry.json` | `%USERPROFILE%\.copilot\skill-registry.json` |

### Git 設定

SSH キーのパスは Windows 形式（`C:\Users\...`）でも Unix 形式（`/c/Users/...`）でも動作する。
