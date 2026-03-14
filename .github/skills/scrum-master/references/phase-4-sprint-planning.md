# Phase 4: スプリントプランニング

> **開始時出力**: `=== PHASE 4: スプリントプランニング 開始 ===`

**前提確認**: `plan.json` が存在し、`current_phase >= 3` であること。`plan.json` が存在しない場合は Phase 2 から再開する。

バックログからスプリントに含めるタスクを選出し、スプリントゴール（Why）を定義してから並列実行グループ（ウェーブ）に分割する。

スクラムガイド2020では、スプリントプランニングは **Why（スプリントゴール）→ What（タスク選出）→ How（ウェーブ分割）** の順で行う。

## 手順

1. 直前スプリントの `process_review` と `next_sprint_actions` を確認し、今回のプランに反映する
   - `next_sprint_actions` に「git-skill-manager refine で [skill-name] を改良する」が含まれる場合は、バックログにタスクを追加してこのスプリントで実行する:
     - `{ action: "git-skill-manager refine で [skill-name] を改良する", skill: "git-skill-manager", done_criteria: "改良が完了し SKILL.md が更新されていること" }`
   - push も必要な場合（「[repo-name] に push する」が含まれる）は、refine タスクに依存する形で push タスクも追加する:
     - `{ action: "git-skill-manager push で [skill-name] を [repo-name] に共有する", skill: "git-skill-manager", done_criteria: "リモートリポジトリにスキルの改良版がプッシュされていること", depends_on: [refineタスクID] }`
2. **スプリントゴール（Why）を定義する**: このスプリントで達成する唯一の目標を1文で定める
   - `product_goal` と残バックログを踏まえて「なぜ今このスプリントを実施するか」を明確にする
   - 例: 「認証機能を完成させ、ユーザーが安全にログインできる状態にする」
   - スプリントゴールはスプリントに一貫性と集中力をもたらす。複数の独立したゴールを設定してはならない
3. priority順にタスクを並べる
4. depends_onの制約を考慮し、先行タスクが未完了のタスクは選出しない
5. 1スプリント = 3〜5タスクを目安にする
6. **ウェーブ分割（How）**: 選出したタスクを依存関係に基づいて実行グループ（ウェーブ）に分割する:
   - **Wave 1**: スプリント内に先行依存がないタスク（depends_onが空、または依存先がすべて前スプリントまでに完了済み）
   - **Wave 2**: Wave 1 のタスクに依存するタスク
   - **Wave N**: Wave N-1 のタスクに依存するタスク
   - 同一ウェーブ内のタスクは**並列実行**される
   - **同一ファイル競合の対処**: 同一ウェーブ内のタスクが同一ファイルを変更する可能性がある場合:
     - **戦略A: ウェーブ分割（デフォルト）** — 変更箇所が密接に絡み合う場合、または判断に迷う場合
     - **戦略B: git worktree 並列実行** — 変更箇所が独立したセクションであることが明確な場合（テンプレート: `subagent-templates.md`「worktree 並列実行時」を参照）
7. プランJSONを生成する（`sprint_goal`・`execution_groups` フィールドを記録）
8. バリデーションを実行する（**最大3回**。3回失敗したらエラー内容をユーザーに提示して修正方針を相談する）:
   ```bash
   python ${SKILL_DIR}/scripts/validate_plan.py plan.json --skills-json skills.json
   ```
   - `plan.json` と `skills.json` はどちらも作業ディレクトリのルートに配置する
9. **プランレビュー（サブエージェントへ委譲）**: バリデーション通過後、**⚠️ `runSubagent` を即時起動する**（テンプレート: `subagent-templates.md`「スプリントプランレビュー時」を使用）。自分でレビューしてはならない
   - レビュー結果に「修正あり」が含まれる場合は修正後の `plan.json` を読み込み直す
   - 「修正なし」の場合はそのまま次のステップへ進む
10. プランをユーザーに表形式で提示して承認を得る:
   ```
   Sprint N プラン

   スプリントゴール（Why）: [sprint_goal]

   | Wave | # | タスク | スキル | 依存 |
   |------|---|--------|--------|------|
   | 1 | b1 | [action] | [skill] | - |
   | 1 | b2 | [action] | [skill] | - |
   | 2 | b3 | [action] | [skill] | b1 |
   | 2 | b4 | [action] | - | b2 |
   | 3 | b5 | [action] | [skill] | b3, b4 |

   並列実行: Wave 1 (2件同時) → Wave 2 (2件同時) → Wave 3 (1件)

   完成の定義: [definition_of_done]

   今回反映した改善点:
   - [next_sprint_action 1]
   - [next_sprint_action 2]

   このプランで進めますか？
   ```
   - 修正があった場合は「今回反映した改善点」に続けて「プランレビューによる修正」欄も表示する:
     ```
     プランレビューによる修正:
     - [修正内容の要約]
     ```

## ゲート条件（Phase 5 に進む前に確認）

- [ ] プランレビュー（ステップ 9）が完了している
- [ ] ユーザーがスプリントプランを承認した
- [ ] `plan.json` の `sprints[]` と `execution_groups` が更新されている

→ 条件を満たしたら **Phase 5: タスク実行** へ進む
