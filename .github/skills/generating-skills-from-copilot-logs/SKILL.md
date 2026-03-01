---
name: generating-skills-from-copilot-logs
description: VSCode Copilot または Claude Code のチャット履歴を分析し、繰り返しワークフローを検出して新しいスキルを自動生成するメタスキル。「履歴からスキルを作って」「Copilotの使い方パターンをスキル化して」「よく使うワークフローをスキルにして」「チャット履歴を分析してスキル候補を提案して」などのリクエストで使用する。git-skill-manager の discover 操作からも自動的に起動される。
metadata:
   version: "1.0"
---

# Generating Skills from Copilot Logs

VSCode Copilot または Claude Code のチャット履歴からAgent Skillを自動生成するメタスキル。

## 起動元

| 呼び出し元 | 起動方法 |
|---|---|
| ユーザー直接 | 「履歴からスキルを作って」「パターンをスキル化して」など |
| git-skill-manager discover | `discover` 操作が `--since` パラメータでこのスキルを自動起動 |
| scrum-master Phase 6 | スプリント完了後のスキル共有提案から起動 |

`git-skill-manager discover` 経由の場合、`--since` パラメータが渡されるため、
前回実行以降の差分のみを分析する（全履歴の再スキャンを回避）。

## 概要

VSCode Copilot のチャット履歴（`workspaceStorage/*/chatSessions/`）または
Claude Code のセッション履歴（`~/.claude/projects/*/`）を分析し、
繰り返しワークフローを検出してスキルを生成する。パターン抽出は3軸で行う:

- **WHAT**: ユーザーの目標（「コミットする」「PRレビューを修正する」）
- **HOW**: 繰り返される手段・手順（「Agentチームで並列実行する」「typecheck → lint の順で進める」）
- **FLOW**: 複数セッションで繰り返されるプロンプト列（「洗い出し → 一括対応 → 微調整/コミット」）

**重要**: HOWをWHATのサブカテゴリに崩さない。3軸を独立して分析する。

---

## フェーズ 1: データ収集（同意取得）

**必ずユーザーの同意を得てからデータを読む。**

> **git-skill-manager の discover 操作経由で起動された場合**: discover 操作のステップ 2 でユーザーの同意を取得済みのため、このフェーズの同意確認をスキップして直接データ取得に進んでよい。

```
「VSCode Copilotのチャット履歴を分析してスキル候補を提案します。
workspaceStorage 内の chatSessions（またはフォールバックとして state.vscdb）を読み取ります。
続行しますか？」
```

同意後、`scripts/extract-copilot-history.py` を使って履歴を取得する:

```powershell
# 通常（過去90日、Copilot + Claude Code 両方）
python scripts/extract-copilot-history.py --days 90 --noise-filter

# git-skill-manager discover 経由（差分のみ）
python scripts/extract-copilot-history.py --since 2026-02-12T00:00:00Z --noise-filter

# Claude Code 履歴のみ
python scripts/extract-copilot-history.py --source claude-code --noise-filter

# 特定のワークスペースに絞る場合
python scripts/extract-copilot-history.py --workspace "C:\Users\you\project" --days 30
```

### 履歴ファイルの場所

**VSCode Copilot:**

| OS | パス |
|---|---|
| Windows | `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\` |
| macOS | `~/Library/Application Support/Code/User/workspaceStorage/*/chatSessions/` |
| Linux | `~/.config/Code/User/workspaceStorage/*/chatSessions/` |

各ワークスペースフォルダの `workspace.json` でプロジェクトパスを確認できる。

**フォールバック**: `chatSessions/` が存在しない場合は `state.vscdb`（SQLite）の `interactive.sessions` キーから取得する。詳細は [references/copilot-history-guide.md](references/copilot-history-guide.md) 参照。

**Claude Code:**

| OS | パス |
|---|---|
| 全OS共通 | `~/.claude/projects/<project-name>/*.jsonl` |

各 `.jsonl` ファイルが1セッションに対応し、`{"role": "user", "content": "..."}` 形式の行が含まれる。`--source claude-code` で Claude Code 履歴のみを対象にできる。

---

## フェーズ 2: パターン抽出

3軸を**独立して**分析する。HOWをWHATのサブカテゴリにまとめない。

### WHAT パターン（ユーザーの目標）

ユーザーメッセージから「何をしたいか」を抽出する。例:
- 「変更を適切な粒度でコミットして」
- 「PRレビューの指摘を修正して」
- 「テストを追加して型チェックを通して」

### HOW パターン（繰り返される手段）

同じ手法が複数セッションで使われている例:
- 「複数エージェントで並列に実行する」
- 「typecheck → lint → commit の順で進める」
- 「エラーログをまず読ませてから修正指示する」

### FLOW パターン（セッション内の手順列）

複数セッションで繰り返されるプロンプト列:
- 「UI/UX改善点洗い出し → 一括対応指示 → 微調整/コミット」が3セッション以上出現

**粒度の判断基準**: 同じ手順・判断基準で処理できるセッション群なら粒度は適切。異なる手順を含むなら分割が必要。

パターン抽出の詳細例は [references/pattern-extraction-examples.md](references/pattern-extraction-examples.md) 参照。

---

## フェーズ 3: スコアリング

各パターンを以下で評価する:

| 評価軸 | 基準 |
|---|---|
| 頻度 | 3セッション以上で出現 |
| 一貫性 | 毎回ほぼ同じ手順 |
| 自動化可能性 | 判断より手順が多い |
| 既存スキルとの重複 | `<SKILLS_BASE>/`（`~/.copilot/skills` または `<workspace-skill-dir>`）を確認 |

重複する既存スキルがある場合は候補から除外するか、既存スキルの改善候補として提示する。

---

## フェーズ 4: 候補提示

内部スコアを隠してシンプルに提示する:

```
以下のスキル候補が見つかりました:

1. **pre-commit-checker** - typecheck/lintをコミット前に自動実行
   出現回数: 8回 / 対象ワークスペース: 2個

2. **pr-review-fixer** - PRレビュー指摘を修正してコミットするフロー
   出現回数: 5回

どのスキルを生成しますか？（複数選択可）
スコープを変更しますか？（特定ワークスペースのみ等）
```

---

## フェーズ 5: SKILL.md 生成

選択されたパターンから SKILL.md を生成する。

**禁止事項:**
- 生のセッション内容をそのまま貼り付けない
- 秘密情報・トークンを含めない（`export KEY=sk-xxx...` → `export KEY=<masked>`）
- ユーザー固有パスをハードコードしない（`/Users/alice/...` → `~/`）
- CLIコマンドの stdout/stderr をそのまま書かない

出力先: `<SKILLS_BASE>/<skill-name>/SKILL.md`（`<SKILLS_BASE>` は `~/.copilot/skills` または `<workspace-skill-dir>`）

生成後、`skill-creator` の手順に従いパッケージ化する。

---

## フェーズ 6: バリデーション

[references/quality-checklist.md](references/quality-checklist.md) に従い品質確認する。

---

## 安全ルール

- 履歴を読む前に**必ず**ユーザーの同意を得る
- 秘密情報（APIキー、トークン等）をマスクする
- 生のセッション内容を機械的にコピーしない
- ワークスペース名・ユーザー名をスキル内で晒さない
