# プランJSONスキーマ

プラン生成時はこのスキーマに従う。`validate_plan.py` で検証できる。

## 目次

- [スキーマ定義](#スキーマ定義)
- [生成例](#生成例)
- [タスク分解の粒度基準](#タスク分解の粒度基準)

## スキーマ定義

```json
{
  "current_phase": "integer (必須) 現在実行中のフェーズ番号 (1〜7)。フェーズ遷移時に必ず更新する",
  "goal": "string (必須) ユーザーの最終目標",
  "backlog": [
    {
      "id": "string (必須) 一意のタスクID。例: b1, b2, b3",
      "action": "string (必須) タスクの具体的な説明",
      "priority": "integer (必須) 1が最高優先。スプリント選出の順序に使う",
      "done_criteria": "string (必須) 完了の定義。何をもって完了とするか",
      "skill": "string|null (必須) 使用するスキル名。汎用タスクはnull",
      "depends_on": ["string (任意) 先行タスクのIDリスト。デフォルト: []"],
      "status": "string (任意) pending|in_progress|completed|failed|skipped。デフォルト: pending",
      "result": "string|null (任意) タスク完了時の結果サマリー"
    }
  ],
  "sprints": [
    {
      "sprint": "integer (必須) スプリント番号。1始まり",
      "task_ids": ["string (必須) このスプリントで実行するタスクIDリスト"],
      "execution_groups": [["string (必須) ウェーブごとのタスクIDリスト。同一配列内は並列実行、配列順は直列実行"]],
      "review": "string|null (任意) スプリントレビューの結果",
      "process_review": "string|null (任意) スプリントプランと実行プロセスの評価",
      "retro": "string|null (任意) レトロスペクティブの改善点",
      "next_sprint_actions": ["string (任意) 次スプリントで実施する改善アクション。最大3件"],
      "impediments": ["string (任意) 検出されたブロッカーのリスト"]
    }
  ],
  "velocity": {
    "completed_per_sprint": ["integer (任意) 各スプリントの完了タスク数"],
    "remaining": "integer (任意) 未完了タスク数"
  }
}
```

## 生成例

```json
{
  "current_phase": 4,
  "goal": "画像リサイズのスキルを作成してGitリポジトリにプッシュする",
  "backlog": [
    {
      "id": "b1",
      "action": "画像リサイズスキルの要件を整理する",
      "priority": 1,
      "done_criteria": "対応フォーマット・リサイズ方式・依存ライブラリが決まっていること",
      "skill": null,
      "depends_on": [],
      "status": "pending",
      "result": null
    },
    {
      "id": "b2",
      "action": "skill-creatorを使って画像リサイズスキルを作成する",
      "priority": 2,
      "done_criteria": "SKILL.md が作成され validate が通ること",
      "skill": "skill-creator",
      "depends_on": ["b1"],
      "status": "pending",
      "result": null
    },
    {
      "id": "b3",
      "action": "git-skill-managerを使ってリポジトリにプッシュする",
      "priority": 3,
      "done_criteria": "リモートリポジトリにスキルがプッシュされていること",
      "skill": "git-skill-manager",
      "depends_on": ["b2"],
      "status": "pending",
      "result": null
    }
  ],
  "sprints": [
    {
      "sprint": 1,
      "task_ids": ["b1", "b2"],
      "execution_groups": [["b1"], ["b2"]],
      "review": null,
      "process_review": null,
      "retro": null,
      "next_sprint_actions": [],
      "impediments": []
    },
    {
      "sprint": 2,
      "task_ids": ["b3"],
      "execution_groups": [["b3"]],
      "review": null,
      "process_review": null,
      "retro": null,
      "next_sprint_actions": [],
      "impediments": []
    }
  ],
  "velocity": {
    "completed_per_sprint": [],
    "remaining": 3
  }
}
```

## タスク分解の粒度基準

- 1タスク = 1スキルの1回の実行
- 「AしてBする」は2タスクに分ける
- スキルが複数の責務を持つ場合でも、1タスクでは1つの責務だけ依頼する
- 汎用タスク（skill: null）は判断・調査・確認など、スキル不要な作業に限定する
