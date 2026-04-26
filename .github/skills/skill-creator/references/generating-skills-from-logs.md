# エージェントログからスキルを生成

VSCode Copilot、Claude Code、または Kiro CLI のエージェントログを分析し、繰り返しワークフローを検出してスキルを生成するワークフロー。

## 目次

- [起動元](#起動元)
- [概要](#概要)
- [フェーズ 1: データ収集（同意取得）](#フェーズ-1-データ収集同意取得)
- [フェーズ 2: パターン抽出](#フェーズ-2-パターン抽出)
- [フェーズ 3: スコアリング](#フェーズ-3-スコアリング)
- [フェーズ 4: 候補提示](#フェーズ-4-候補提示)
- [フェーズ 5: SKILL.md 生成](#フェーズ-5-skillmd-生成)
- [フェーズ 6: バリデーション](#フェーズ-6-バリデーション)
- [安全ルール](#安全ルール)

---

## 起動元

| 呼び出し元 | 起動方法 |
|---|---|
| skill-creator | 「履歴からスキルを作って」「パターンをスキル化して」など |
| git-skill-manager discover | `discover` 操作が `--since` パラメータでこのワークフローを自動起動 |
| scrum-master Phase 6 | スプリント完了後のスキル共有提案から起動 |

`git-skill-manager discover` 経由の場合、`--since` パラメータが渡されるため、
前回実行以降の差分のみを分析する（全履歴の再スキャンを回避）。

## 概要

VSCode Copilot のチャット履歴（`workspaceStorage/*/chatSessions/`）、
Claude Code のセッション履歴（`~/.claude/projects/*/`）、または
Kiro CLI のログ（`~/.kiro/`）を分析し、
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
「AIエージェントのチャット履歴を分析してスキル候補を提案します。
対象: VSCode Copilot chatSessions / Claude Code JSONL / Kiro CLI ログ
続行しますか？」
```

同意後、`scripts/extract-copilot-history.py` を使って履歴を取得する:

```bash
# 通常（過去90日、全ソース）
python scripts/extract-copilot-history.py --days 90 --noise-filter

# git-skill-manager discover 経由（差分のみ）
python scripts/extract-copilot-history.py --since 2026-02-12T00:00:00Z --noise-filter

# Claude Code 履歴のみ
python scripts/extract-copilot-history.py --source claude-code --noise-filter

# Kiro CLI 履歴のみ
python scripts/extract-copilot-history.py --source kiro-cli --noise-filter

# 特定のワークスペースに絞る場合
python scripts/extract-copilot-history.py --workspace "my-project" --days 30
```

### ログファイルの場所

**VSCode Copilot:**

| OS | パス |
|---|---|
| Windows | `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\` |
| macOS | `~/Library/Application Support/Code/User/workspaceStorage/*/chatSessions/` |
| Linux | `~/.config/Code/User/workspaceStorage/*/chatSessions/` |

各ワークスペースフォルダの `workspace.json` でプロジェクトパスを確認できる。`chatSessions/` が存在しない場合は `state.vscdb`（SQLite）の `interactive.sessions` キーから取得する。

データ形式（`chatSessions/` 内の JSON）:

```json
{
  "requests": [
    {
      "message": {"text": "テストを追加してください"},
      "timestamp": 1700000000000
    }
  ]
}
```

**Claude Code:**

| OS | パス |
|---|---|
| 全OS共通 | `~/.claude/projects/<project-name>/*.jsonl` |

各 `.jsonl` ファイルが1セッションに対応し、`{"role": "user", "content": "..."}` 形式の行が含まれる。

**Kiro CLI（v2）:**

| OS | パス |
|---|---|
| Linux / WSL | `~/.local/share/kiro-cli/data.sqlite3` |
| macOS | `~/Library/Application Support/kiro-cli/data.sqlite3` |
| Windows | `%LOCALAPPDATA%\kiro-cli\data.sqlite3` |

SQLite DB で管理される。テーブル名は `conversations_v2`。ディレクトリパスをキーとしてセッションが保存される。

```
conversations_v2 テーブルの主要カラム:
  id               – セッション UUID
  directory        – セッション開始ディレクトリ
  messages         – JSON 配列
  created_at       – 作成タイムスタンプ
  updated_at       – 更新タイムスタンプ
```

messages の形式:

```json
[
  {"role": "user",      "content": "テストを追加してください", "timestamp": 1700000000},
  {"role": "assistant", "content": "...", "timestamp": 1700000001}
]
```

セッション内で `/save <path>` でJSON形式にエクスポート、`/load <path>` でインポートできる。

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

パターン抽出の詳細例は [pattern-extraction-examples.md](pattern-extraction-examples.md) 参照。

---

## フェーズ 3: スコアリング

各パターンを以下で評価する:

| 評価軸 | 基準 |
|---|---|
| 頻度 | 3セッション以上で出現 |
| 一貫性 | 毎回ほぼ同じ手順 |
| 自動化可能性 | 判断より手順が多い |
| 既存スキルとの重複 | `<SKILLS_BASE>/`（`<AGENT_HOME>/skills` または `<workspace-skill-dir>`）を確認 |

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

出力先: `<SKILLS_BASE>/<skill-name>/SKILL.md`（`<SKILLS_BASE>` は `<AGENT_HOME>/skills` または `<workspace-skill-dir>`）

生成後、skill-creator のパッケージ手順に従いパッケージ化する。

---

## フェーズ 6: バリデーション

`quality-checklist.md` に従い品質確認する。

---

## 安全ルール

- 履歴を読む前に**必ず**ユーザーの同意を得る
- 秘密情報（APIキー、トークン等）をマスクする
- 生のセッション内容を機械的にコピーしない
- ワークスペース名・ユーザー名をスキル内で晒さない
