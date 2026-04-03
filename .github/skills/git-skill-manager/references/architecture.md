# アーキテクチャとパス定義

## ディレクトリ構成

```
ローカル（Windows PowerShell）
─────────────────────────────────────────
  <AGENT_HOME>\skills\          ← スキルインストール先
    ├── skill-a\SKILL.md
    ├── skill-b\SKILL.md  (enabled)
    └── skill-c\SKILL.md  (disabled → メタデータ非ロード)

  <AGENT_HOME>\skill-registry.json  ← レジストリ

  <AGENT_HOME>\cache\           ← リポジトリキャッシュ（永続）
    ├── team-skills\                      ← 初回clone、以降はfetch
    └── personal\
─────────────────────────────────────────
         │ pull              │ pull + push
         ▼                   ▼
  ┌────────────────┐  ┌────────────────┐
  │ repo: team     │  │ repo: personal │
  │ (readonly)     │  │ (read/write)   │
  │ priority: 1    │  │ priority: 2    │
  └────────────────┘  └────────────────┘
```

## パス定義

すべての操作で以下のパスを使う。

```powershell
# <AGENT_HOME> はエージェント別ホームディレクトリ（例: $env:USERPROFILE\.claude）
$SKILL_HOME   = "<AGENT_HOME>\skills"
$REGISTRY     = "<AGENT_HOME>\skill-registry.json"
$CACHE_DIR    = "<AGENT_HOME>\cache"
```

初回は `$SKILL_HOME` と `$CACHE_DIR` ディレクトリを作成する:

```powershell
if (-not (Test-Path $SKILL_HOME)) { New-Item -ItemType Directory -Path $SKILL_HOME -Force }
if (-not (Test-Path $CACHE_DIR))  { New-Item -ItemType Directory -Path $CACHE_DIR -Force }
```
