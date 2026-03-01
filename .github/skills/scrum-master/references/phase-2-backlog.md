# Phase 2: バックログ作成

ユーザーのプロンプトからバックログを作成する。曖昧な指示の場合は requirements-definer を介して要件を明確化してからバックログに変換する。

## Step 2-0: requirements.json の存在チェック

作業ディレクトリのルートに `requirements.json` が存在するか確認する。

- **存在する** → Step 2-3（requirements.json → バックログ変換）へスキップする
- **存在しない** → Step 2-1（曖昧度判定）へ進む

## Step 2-1: 曖昧度判定

ユーザーのプロンプトを以下の4項目で評価する:

| # | 判定項目 | 明確の例 | 曖昧の例 |
|---|----------|---------|---------|
| a | ゴールが1文で定義可能か | 「REST APIにページネーションを実装して」 | 「ECサイト作って」 |
| b | 対象ユーザー/利用シーンが特定できるか | 「管理者画面のダッシュボード」 | 「Webアプリで」 |
| c | スコープ（In/Out）が推定可能か | 「src/api/users.tsに追加」 | 「いい感じに」 |
| d | 3タスク以内で分解可能か | 「バリデーションを追加してテストを書いて」 | 機能数が不明 |

**判定ルール:**

- **4項目すべて明確 かつ 既存機能への単純な追加・修正である** → Step 2-4（従来の直接バックログ作成）へ
- **上記以外（1項目でも不明確、または新規プロダクト・新規機能開発）** → Step 2-2（requirements-definer 呼び出し）へ

> **重要**: 迷ったら requirements-definer を発動すること。LLM はプロンプトを「明確」と解釈しがちだが、新規開発・新規機能追加は対象ユーザーやスコープが暗黙的に不明確なケースが多い。Step 2-4 に進んでよいのは「既存ファイルへの具体的な追加・修正」のような小さく明示的なタスクに限る。

## Step 2-2: requirements-definer 呼び出し

サブエージェントを起動する（Claude Code: Task ツール / GitHub Copilot: `#tool:agent/runSubagent`）（テンプレート「requirements-definer 呼び出し時」を使用）。

完了後、`requirements.json` が生成される。Step 2-3 へ進む。

## Step 2-3: requirements.json → バックログ変換

`requirements.json` を読み込み、以下のマッピングルールで `plan.json` のバックログに変換する:

| requirements.json | plan.json | 変換ルール |
|---|---|---|
| `goal` | `goal` | そのまま転記 |
| `functional_requirements[].user_story` or `description` | `backlog[].action` | ストーリーまたは説明から具体的な作業内容を導出 |
| `functional_requirements[].acceptance_criteria` | `backlog[].done_criteria` | Given/When/Then を検証可能な完了条件に変換 |
| `functional_requirements[].moscow` or 出現順 | `backlog[].priority` | must=1, should=2, could=3。moscow がなければ出現順 |
| `non_functional_requirements` | 横断タスクまたは制約 | パフォーマンス要件 → 専用タスク、それ以外 → 各タスクの done_criteria に制約として付与 |
| `scope.out` | バックログに含めない | 除外スコープとして記録（変換しない） |

**変換の粒度:**
- 1つの functional_requirement が複数タスクに分解されることがある（1タスク = 1スキルの1回の実行）
- acceptance_criteria が複数ある場合、同一タスクの done_criteria に統合するか、テスト用の別タスクに分離する

変換後、Step 2-5 へ進む。

## Step 2-4: 従来のバックログ作成

プロンプトから直接バックログを作成する（requirements-definer を経由しないパス）。

1. ゴール（最終目標）を1文で定義する
2. ゴール達成に必要な全タスクを洗い出す
3. 各タスクに以下を設定する:
   - **action**: 具体的な作業内容
   - **priority**: 実行優先度（1が最高）
   - **done_criteria**: 完了の定義
   - **skill**: Phase 1のスキル一覧からマッチするスキル名。該当なしはnull
   - **depends_on**: 先行タスクのID

Step 2-5 へ進む。

## Step 2-5: プランJSON保存

スキーマ詳細は [plan-schema.md](plan-schema.md) を参照する。プランJSONは **作業ディレクトリのルートに `plan.json` として保存する**。

**プランJSON最小スケルトン（この形式で生成すること）:**
```json
{
  "current_phase": 2,
  "goal": "...",
  "requirements_source": "direct",
  "backlog": [
    {"id": "b1", "action": "...", "priority": 1, "done_criteria": "...", "skill": "...", "depends_on": [], "status": "pending", "result": null}
  ],
  "sprints": [],
  "velocity": {"completed_per_sprint": [], "remaining": 0}
}
```

- `requirements_source`: バックログの出自を記録する。値は `"requirements-definer"`（requirements.json 経由）または `"direct"`（プロンプトから直接作成）のいずれか

**タスク分解の粒度:**
- 1タスク = 1スキルの1回の実行
- 「AしてBする」は2タスクに分ける
- 汎用タスク（skill: null）は判断・調査・確認など、スキル不要な軽微な作業に限定する

**▶ Phase 2 完了チェック（必須）**: `plan.json` がルートに保存されたことを確認してから Phase 3 へ進む。`plan.json` が存在しない場合は保存してから進む。**`plan.json` なしで Phase 3 以降に進んではならない。**
