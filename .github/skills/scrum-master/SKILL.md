---
name: scrum-master
description: ユーザーのプロンプトをタスク分解し、サブエージェントにスキルを委譲してスプリント単位で実行するオーケストレーター。「スクラムして」「スプリントで進めて」「タスク分解して実行して」「チームで開発して」「バックログを作って進めて」「要件整理してから開発して」「何を作るか一緒に考えて」などで発動。曖昧な指示は要件定義フェーズで対話しながら明確化する。スキル不足時はskill-creatorで作成し、git-skill-managerでリポジトリ共有も可能。
metadata:
  version: "1.0"
---

# scrum-master

ユーザーのプロンプトをバックログに分解し、スプリント単位でサブエージェントに委譲して実行するオーケストレーター。

## 実行原則（必須）

* フェーズは**1 → 7**を順番に実行する。飛ばさない。
* 各フェーズ終了時に `plan.json` の `current_phase` を更新する。
* `plan.json` が存在しない状態で Phase 3 以降に進んではならない。
* フェーズ開始前に必ず対応する `references/phase-N-*.md` を読み、手順を理解する。
* バックログタスクは原則全てサブエージェントで実行する。
* サブエージェントへの指示は必ずテンプレートを使用する。テンプレートがない場合は新規作成を検討する。

## フェーズ一覧

| # | フェーズ | 概要 | 前提 | 詳細 |
|---|----------|------|------|------|
| 1 | スキル探索 | 利用可能なスキル一覧を取得する | — | [phase-1-skill-discovery.md](references/phase-1-skill-discovery.md) |
| 2 | バックログ作成 | 要件を明確化し `plan.json` を生成する | — | [phase-2-backlog.md](references/phase-2-backlog.md) |
| 3 | スキルギャップ解決 | スキル不足・改良が必要なタスクを解消する | `plan.json` 存在 | [phase-3-skill-gap.md](references/phase-3-skill-gap.md) |
| 4 | スプリントプランニング | タスクをウェーブに分割しユーザー承認を得る | `current_phase >= 3` | [phase-4-sprint-planning.md](references/phase-4-sprint-planning.md) |
| 5 | タスク実行 | ウェーブ単位でサブエージェントを並列起動する | Sprint計画承認済 | [phase-5-task-execution.md](references/phase-5-task-execution.md) |
| 6 | スプリントレビュー | レビュー・フィードバック・スキル評価を行う | Sprint完了/中断 | [phase-6-sprint-review.md](references/phase-6-sprint-review.md) |
| 7 | 進捗レポートと継続判断 | ユーザーに報告し次アクションを確認する | Phase 6 完了 | [phase-7-progress-report.md](references/phase-7-progress-report.md) |

## フェーズ実行手順

各フェーズを開始する際は、必ず以下の手順に従う:

1. 対応する `references/phase-N-*.md` を読む
2. 前提条件（`plan.json` の存在・`current_phase` の値）を確認する
3. 詳細手順に従って実行する
4. 完了チェックをクリアしてから次のフェーズへ進む

## サブエージェントへの指示テンプレート

テンプレート本文は [references/subagent-templates.md](references/subagent-templates.md) を参照すること。

**重要**:
- SKILL.md の内容をプロンプトに埋め込まない。ファイルパスだけ渡し、サブエージェント自身に読ませる。
- すべてのテンプレートに戻り値の形式指定を含める。

| テンプレート | 用途 |
|---|---|
| requirements-definer 呼び出し時 | 要件定義の実行 |
| skill: null タスク実行時 | スキル不要な調査・確認・軽微な編集 |
| スキル実行時 | 既存スキルを使ったタスク実行 |
| スキル作成時 | skill-creator でスキルを新規作成 |
| スキル改良時 | skill-creator でスキルを改良・分割 |
| コードベースからスキル生成時 | codebase-to-skill でスキルを生成 |
| スキル招募時 | skill-recruiter で外部スキルを取得 |
| スプリントレビュー時 | sprint-reviewer でレビューを実施 |
| スキルフィードバック収集時 | 使用スキルのフィードバックを収集・記録 |
| スキル昇格時 | git-skill-manager promote を実行 |
| スキル評価時 | skill-evaluator でスキルを評価 |
| スキル共有時 | git-skill-manager push を実行 |
| スキル発見時 | git-skill-manager discover を実行 |
| worktree 並列実行時 | 同一ファイルを独立セクションで並列変更する場合 |

## 動作環境

- **GitHub Copilot Chat**（Windows / macOS / Linux）および **Claude Code** で動作する
- スクリプト実行は `python`（環境によっては `python3`）
- パス区切りは `/` を使用する（クロスプラットフォームで動作する）
- ファイル書き出し時の文字コードは **UTF-8 without BOM**

| 環境 | サブエージェント起動方法 |
|------|------------------------|
| Claude Code | `Task` ツールを使用。`subagent_type: "general-purpose"` を指定する |
| GitHub Copilot | `#tool:agent/runSubagent` を使用する |

並列実行（同一ウェーブ）は、Claude Code では複数の Task ツール呼び出しを単一メッセージに並べることで実現する。

## エラーハンドリング

| 状況 | 対応 |
|---|---|
| discover_skills.py 実行失敗 | .github/skills/ ディレクトリの存在を確認。なければ作成を提案する |
| validate_plan.py バリデーション失敗 | エラー内容に従ってプランJSONを修正し再検証する |
| サブエージェント実行失敗 | ユーザーにリトライ/スキップ/中断の選択肢を提示する |
| 全タスク失敗 | ゴール自体の実現可能性をユーザーと再検討する |
