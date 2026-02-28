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
  "requirements_source": "string (任意) バックログの出自。'requirements-definer' = requirements.json 経由、'direct' = プロンプトから直接作成。デフォルト: 'direct'",
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
  "requirements_source": "direct",
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

## requirements.json → plan.json 変換ルール

`requirements.json`（requirements-definer の出力）から `plan.json` のバックログに変換する際のマッピングルール。

### フィールドマッピング

| requirements.json | plan.json | 変換ルール |
|---|---|---|
| `goal` | `goal` | そのまま転記 |
| — | `requirements_source` | `"requirements-definer"` を設定 |
| `functional_requirements[]` | `backlog[]` | 各要件を1つ以上のタスクに分解（1タスク = 1スキル実行） |
| `.user_story` or `.description` | `backlog[].action` | ストーリーまたは説明から具体的な作業内容を導出 |
| `.acceptance_criteria[]` | `backlog[].done_criteria` | Given/When/Then を検証可能な完了条件に要約 |
| `.moscow` or 出現順 | `backlog[].priority` | must=1, should=2, could=3。moscow がなければ出現順 |
| `non_functional_requirements[]` | 横断タスクまたは制約 | 下記参照 |
| `scope.out[]` | バックログに含めない | 除外スコープとして記録のみ |

### 非機能要件の扱い

| 非機能要件の種類 | 変換方法 |
|---|---|
| パフォーマンス・負荷テスト | 専用のテスト/検証タスクを作成（例: 「APIレスポンスタイムの計測とチューニング」） |
| セキュリティ | 実装タスクの done_criteria に制約として付与（例: 「SQLインジェクション対策が施されていること」） |
| 可用性・信頼性 | インフラ構成タスクを作成するか、デプロイ/設定タスクの done_criteria に含める |
| その他 | 関連する機能タスクの done_criteria に制約として付与 |

### 変換例

**requirements.json:**
```json
{
  "goal": "個人向けTODO管理WebアプリをReactで構築する",
  "functional_requirements": [
    {
      "id": "F-01",
      "name": "TODO作成",
      "user_story": "As a 個人ユーザー, I want タイトル・期限・優先度を指定してTODOを登録する, so that やるべきことを忘れずに管理できる",
      "moscow": "must",
      "acceptance_criteria": [
        {"given": "ユーザーがログイン済み", "when": "タイトル・期限・優先度を入力して送信", "then": "TODOが一覧に追加される"},
        {"given": "ユーザーがログイン済み", "when": "タイトルを空のまま送信", "then": "バリデーションエラーが表示される"}
      ]
    }
  ],
  "non_functional_requirements": [
    {"id": "N-01", "name": "レスポンス", "description": "API応答は95パーセンタイルで500ms以内"}
  ]
}
```

**変換後の plan.json バックログ（抜粋）:**
```json
{
  "goal": "個人向けTODO管理WebアプリをReactで構築する",
  "requirements_source": "requirements-definer",
  "backlog": [
    {
      "id": "b1",
      "action": "TODO作成フォームのUIコンポーネントを実装する（タイトル・期限・優先度の入力、バリデーション付き）",
      "priority": 1,
      "done_criteria": "フォーム送信でTODOが一覧に追加される。タイトル空送信時にバリデーションエラーが表示される",
      "skill": "react-frontend-coder",
      "depends_on": [],
      "status": "pending",
      "result": null
    },
    {
      "id": "b2",
      "action": "TODO作成フォームのユニットテストを作成する",
      "priority": 1,
      "done_criteria": "正常系・バリデーションエラー系のテストがパスする。API応答500ms以内の制約を考慮したモック設計",
      "skill": "react-frontend-unit-tester",
      "depends_on": ["b1"],
      "status": "pending",
      "result": null
    }
  ]
}
```
