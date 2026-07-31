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
  "product_goal": "string (必須) プロダクトバックログのコミットメント。プロダクトの将来の状態を1文で表す長期目標。goal より大きなビジョン（例: 'チームの生産性を2倍にするCI/CDプラットフォームを提供する'）。スクラムガイド2020の確約。Phase 2 Step 2-5 で設定する",
  "definition_of_done": "string (必須) インクリメントのコミットメント。スプリントで完成したと見なす共通の品質基準（例: 'テストが全て通過・コードレビュー済み・本番環境にデプロイ可能な状態'）。スクラムガイド2020の確約。Phase 2 Step 2-5 で設定する",
  "requirements_source": "string (任意) バックログの出自。'requirements-definer' = requirements.md 経由、'direct' = プロンプトから直接作成。デフォルト: 'direct'",
  "backlog": [
    {
      "id": "string (必須) 一意のタスクID。例: b1, b2, b3",
      "action": "string (必須) タスクの具体的な説明",
      "priority": "integer (必須) 1が最高優先。スプリント選出の順序に使う",
      "done_criteria": "string (必須) 完了の定義。何をもって完了とするか",
      "skill": "string|string[]|null (必須) プライマリスキル名。skill-selector の primary_skills[].name から導出する。補助スキルは含めない。汎用タスクはnull",
      "selection": {
        "source": "string (任意) 推薦元。通常は 'skill-selector'",
        "supporting_skills": {
          "principle": {
            "mode": "string (必須) skill|fallback|none",
            "name": "string|null (任意) 推薦された補助スキル名",
            "instruction": "string|null (任意) natural language fallback instruction",
            "timing": "string|null (任意) before-primary|after-primary",
            "reason": "string|null (任意) 推薦理由"
          },
          "conditional": {
            "mode": "string (必須) skill|fallback|none",
            "name": "string|null (任意) 推薦された補助スキル名",
            "instruction": "string|null (任意) natural language fallback instruction",
            "timing": "string|null (任意) before-primary|after-primary",
            "reason": "string|null (任意) 推薦理由"
          }
        },
        "notes": ["string (任意) 注意事項"]
      },
      "depends_on": ["string (任意) 先行タスクのIDリスト。デフォルト: []"],
      "status": "string (任意) pending|in_progress|completed|failed|skipped。デフォルト: pending",
      "result": "string|null (任意) タスク完了時の結果サマリー",
      "review_result": "object|null (任意) agent-reviewer の集約レビュー結果。verdict-json をそのまま保持してよい"
    }
  ],
  "sprints": [
    {
      "sprint": "integer (必須) スプリント番号。1始まり",
      "sprint_goal": "string (必須) スプリントバックログのコミットメント。このスプリントで達成する唯一の目標（Why）。スクラムガイド2020の確約",
      "task_ids": ["string (必須) このスプリントで実行するタスクIDリスト"],
      "execution_groups": [["string (必須) ウェーブごとのタスクIDリスト。同一配列内は並列実行、配列順は直列実行"]],
      "increment": "string|null (任意) このスプリントで完成したインクリメントの説明。DoD を満たすリリース可能な成果物",
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
  "product_goal": "誰でも再利用できる画像処理スキルライブラリをチームに提供する",
  "definition_of_done": "テストが全て通過し、コードレビュー済みで、ドキュメントが整備されていること",
  "requirements_source": "direct",
  "backlog": [
    {
      "id": "b1",
      "action": "画像リサイズスキルの要件を整理する",
      "priority": 1,
      "done_criteria": "対応フォーマット・リサイズ方式・依存ライブラリが決まっていること",
      "skill": null,
      "selection": {
        "source": "skill-selector",
        "supporting_skills": {
          "principle": {"mode": "none", "name": null, "instruction": null, "timing": null, "reason": null},
          "conditional": {"mode": "none", "name": null, "instruction": null, "timing": null, "reason": null}
        },
        "notes": []
      },
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
      "selection": {
        "source": "skill-selector",
        "supporting_skills": {
          "principle": {"mode": "skill", "name": "self-checking", "instruction": null, "timing": "after-primary", "reason": "成果物の自己評価が必要"},
          "conditional": {"mode": "none", "name": null, "instruction": null, "timing": null, "reason": null}
        },
        "notes": []
      },
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
      "selection": {
        "source": "skill-selector",
        "supporting_skills": {
          "principle": {"mode": "none", "name": null, "instruction": null, "timing": null, "reason": null},
          "conditional": {"mode": "none", "name": null, "instruction": null, "timing": null, "reason": null}
        },
        "notes": []
      },
      "depends_on": ["b2"],
      "status": "pending",
      "result": null
    }
  ],
  "sprints": [
    {
      "sprint": 1,
      "sprint_goal": "画像リサイズスキルの要件を固め、SKILL.md を完成させる",
      "task_ids": ["b1", "b2"],
      "execution_groups": [["b1"], ["b2"]],
      "increment": null,
      "review": null,
      "process_review": null,
      "retro": null,
      "next_sprint_actions": [],
      "impediments": []
    },
    {
      "sprint": 2,
      "sprint_goal": "スキルをリモートリポジトリに公開し、チームが利用可能な状態にする",
      "task_ids": ["b3"],
      "execution_groups": [["b3"]],
      "increment": null,
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

- 基本は 1タスク = 1スキルの1回の実行
- 「AしてBする」で責務が異なる場合は2タスクに分ける
- 密接に連携するスキルを1タスクで組み合わせたい場合は `skill` を配列で指定できる（例: `["react-frontend-coder", "webapp-testing"]`）
- 配列指定の場合、サブエージェントは先頭スキルから順にSKILL.mdを読んで実行する
- 汎用タスク（skill: null）は判断・調査・確認など、スキル不要な作業に限定する

## レビュー結果の保持

- レビューの perspective 選択は orchestrator ではなく `agent-reviewer` が行う
- `review_result` には agent-reviewer の集約結果をそのまま保持する

## requirements.json → plan.json 変換ルール

`requirements.json`（`convert_requirements.py` が `requirements.md` から生成）から `plan.json` のバックログに変換する際のマッピングルール。

### フィールドマッピング

| requirements.json フィールド | plan.json | 変換ルール |
|---|---|---|
| `goal` | `goal` | そのまま転記 |
| — | `requirements_source` | `"requirements-definer"` を設定 |
| `functional_requirements[]` | `backlog[]` | 各要件を1つ以上のタスクに分解（1タスク = 1スキル実行） |
| `functional_requirements[].user_story` | `backlog[].action` | ストーリーから具体的な作業内容を導出 |
| `functional_requirements[].acceptance_criteria[].then` | `backlog[].done_criteria` | Given/When/Then を検証可能な完了条件に要約 |
| `functional_requirements[].moscow` または出現順 | `backlog[].priority` | must=1, should=2, could=3。未設定なら出現順 |
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

**requirements.json（抜粋）:**
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
      "action": "TODO作成フォームのUIコンポーネントを実装し、内包するユニットテストまで完了する",
      "priority": 1,
      "done_criteria": "フォーム送信でTODOが一覧に追加される。タイトル空送信時にバリデーションエラーが表示される。正常系・バリデーションエラー系のテストがパスする",
      "skill": "react-frontend-coder",
      "selection": {
        "source": "skill-selector",
        "supporting_skills": {
          "principle": {"mode": "skill", "name": "self-checking", "instruction": null, "timing": "after-primary", "reason": "実装成果物の自己評価が必要"},
          "conditional": {"mode": "skill", "name": "test-driven-development", "instruction": null, "timing": "before-primary", "reason": "受け入れ条件を先にテスト化できる"}
        },
        "notes": []
      },
      "depends_on": [],
      "status": "pending",
      "result": null
    }
  ]
}
```

> **役割分離**: `skill` はプライマリスキル、`selection` は skill-selector が返した補助スキル・注意事項を表す。レビューは `agent-reviewer` をオーケストレーターが直接起動する。

> **注**: 上記の例では実装とユニットテストを `react-frontend-coder` に統合している。ブラウザ実機確認まで含めたい場合は `webapp-testing` を追加する。分離したい場合は従来通り個別タスクに分ける:

```json
{
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
      "action": "TODO作成フォームのブラウザ挙動を E2E 観点で確認する",
      "priority": 1,
      "done_criteria": "正常系・バリデーションエラー系の操作がブラウザ上で成立する。主要エラー表示と入力導線を確認できる",
      "skill": "webapp-testing",
      "depends_on": ["b1"],
      "status": "pending",
      "result": null
    }
  ]
}
```
