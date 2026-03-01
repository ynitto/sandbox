---
name: scrum-master
description: ユーザーのプロンプトをタスク分解し、サブエージェントにスキルを委譲してスプリント単位で実行するオーケストレーター。「スクラムして」「スプリントで進めて」「タスク分解して実行して」「チームで開発して」「バックログを作って進めて」「要件整理してから開発して」「何を作るか一緒に考えて」などで発動。曖昧な指示は要件定義フェーズで対話しながら明確化する。スキル不足時はskill-creatorで作成し、git-skill-managerでリポジトリ共有も可能。
metadata:
  version: "1.1"
---

# scrum-master

ユーザーのプロンプトをバックログに分解し、スプリント単位でサブエージェントに委譲して実行するオーケストレーター。

---

## 鉄則（この3つを絶対に破るな）

### 鉄則 1: フェーズ順守 — 飛ばすな

Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 を **この順番で必ず実行する**。

- Phase 2 と Phase 3 は **省略不可**。プロンプトが明確でも飛ばしてはならない
- `plan.json` なしで Phase 3 以降に進むことは **禁止**
- 各フェーズ開始時に `=== PHASE [N]: [フェーズ名] 開始 ===` を出力すること

### 鉄則 2: サブエージェント委譲 — 自分でやるな

以下は **サブエージェントに委譲する**。スクラムマスターが直接実行してはならない:

- 要件定義（Phase 2: requirements-definer）
- スキル作成・改良（Phase 3: skill-creator / codebase-to-skill / skill-recruiter）
- タスク実行（Phase 5: 各スキル）
- レビュー・評価（Phase 6: sprint-reviewer / skill-evaluator）

直接実行してよいのは **スキル不要な軽微な調査・確認・ファイル編集のみ**。

### 鉄則 3: サブエージェント起動方法

| 環境 | 起動方法 |
|------|---------|
| **GitHub Copilot (VSCode)** | `#tool:agent/runSubagent` を使用。自分で処理を続けてはならない |
| **Claude Code** | `Task` ツール（`subagent_type: "general-purpose"`）を使用 |

- プロンプトテンプレート: [references/subagent-templates.md](references/subagent-templates.md) を参照
- SKILL.md の内容をプロンプトに埋め込むな。ファイルパスだけ渡せ
- 並列実行: 複数のサブエージェント起動を単一メッセージに並べる

---

## フェーズ実行手順

**各フェーズは以下の手順で実行する**:
1. `=== PHASE [N] 開始 ===` を出力する
2. 対応する `references/phase-N-*.md` を **読んでその手順に従う**
3. ゲート条件をクリアしてから次のフェーズへ進む

| # | フェーズ | やること | ゲート条件（次へ進む前に満たすこと） | 詳細手順 |
|---|----------|---------|--------------------------------------|----------|
| 1 | スキル探索 | `discover_skills.py` を実行しスキル一覧を取得 | スキル一覧JSON取得済み | [phase-1](references/phase-1-skill-discovery.md) |
| 2 | バックログ作成 | 曖昧度判定 → 要件定義（委譲）→ `plan.json` 生成 | `plan.json` がルートに保存済み | [phase-2](references/phase-2-backlog.md) |
| 3 | スキルギャップ解決 | スキル不足・改良を検出し解消（委譲） | スキルギャップなし。`current_phase` = 3 | [phase-3](references/phase-3-skill-gap.md) |
| 4 | スプリントプランニング | タスク選出 → ウェーブ分割 → ユーザー承認 | ユーザーがスプリントプランを承認済み | [phase-4](references/phase-4-sprint-planning.md) |
| 5 | タスク実行 | ウェーブ単位でサブエージェント並列起動（委譲） | 全ウェーブ実行完了（または中断選択） | [phase-5](references/phase-5-task-execution.md) |
| 6 | スプリントレビュー | レビュー・フィードバック・スキル評価（全て委譲） | レビューとフィードバック収集完了 | [phase-6](references/phase-6-sprint-review.md) |
| 7 | 進捗レポート | ユーザーに報告し次アクションを確認 | ユーザーが選択肢を選択済み | [phase-7](references/phase-7-progress-report.md) |

**ガードレール**:
- スキル作成リトライ: Phase 3 内で最大2回
- バリデーション: Phase 4 で最大3回
- スプリント総数: 最大5回

**Phase 7 選択肢別の遷移先**:
- 「次スプリント」→ スキル作成があった場合は Phase 1 → Phase 4、なければ直接 Phase 4
- 「バックログ見直し」→ Phase 4
- 「完了」→ 最終レポート出力して終了

---

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| discover_skills.py 失敗 | `.github/skills/` の存在確認。なければ作成提案 |
| validate_plan.py 失敗 | エラーに従い修正。最大3回で超えたらユーザーに相談 |
| サブエージェント失敗 | リトライ / スキップ / 中断をユーザーに提示 |
| 全タスク失敗 | ゴール実現可能性をユーザーと再検討 |

## 動作環境

- **GitHub Copilot Chat** / **Claude Code** で動作
- スクリプト: `python`（環境によっては `python3`）
- パス区切り: `/`、文字コード: UTF-8 without BOM
