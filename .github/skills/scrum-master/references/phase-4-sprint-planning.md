# Phase 4: スプリントプランニング

> **開始時出力**: `=== PHASE 4: スプリントプランニング 開始 ===`

**前提確認**: `plan.json` が存在し、`current_phase >= 3` であること。`plan.json` が存在しない場合は Phase 2 から再開する。

バックログからスプリントに含めるタスクを選出し、並列実行グループ（ウェーブ）に分割する。

## 手順

1. 直前スプリントの `process_review` と `next_sprint_actions` を確認し、今回のプランに反映する
   - `next_sprint_actions` に「git-skill-manager refine で [skill-name] を改良する」が含まれる場合は、バックログにタスクを追加してこのスプリントで実行する:
     - `{ action: "git-skill-manager refine で [skill-name] を改良する", skill: "git-skill-manager", done_criteria: "改良が完了し SKILL.md が更新されていること" }`
   - push も必要な場合（「[repo-name] に push する」が含まれる）は、refine タスクに依存する形で push タスクも追加する:
     - `{ action: "git-skill-manager push で [skill-name] を [repo-name] に共有する", skill: "git-skill-manager", done_criteria: "リモートリポジトリにスキルの改良版がプッシュされていること", depends_on: [refineタスクID] }`
2. priority順にタスクを並べる
3. depends_onの制約を考慮し、先行タスクが未完了のタスクは選出しない
4. 1スプリント = 3〜5タスクを目安にする
5. **ウェーブ分割**: 選出したタスクを依存関係に基づいて実行グループ（ウェーブ）に分割する:
   - **Wave 1**: スプリント内に先行依存がないタスク（depends_onが空、または依存先がすべて前スプリントまでに完了済み）
   - **Wave 2**: Wave 1 のタスクに依存するタスク
   - **Wave N**: Wave N-1 のタスクに依存するタスク
   - 同一ウェーブ内のタスクは**並列実行**される
   - **同一ファイル競合の対処**: 同一ウェーブ内のタスクが同一ファイルを変更する可能性がある場合:
     - **戦略A: ウェーブ分割（デフォルト）** — 変更箇所が密接に絡み合う場合、または判断に迷う場合
     - **戦略B: git worktree 並列実行** — 変更箇所が独立したセクションであることが明確な場合（テンプレート: `subagent-templates.md`「worktree 並列実行時」を参照）
6. プランJSONを生成する（`execution_groups` フィールドにウェーブを記録）
7. バリデーションを実行する（**最大3回**。3回失敗したらエラー内容をユーザーに提示して修正方針を相談する）:
   ```bash
   python ${SKILL_DIR}/scripts/validate_plan.py plan.json --skills-json skills.json
   ```
   - `plan.json` と `skills.json` はどちらも作業ディレクトリのルートに配置する
8. プランをユーザーに表形式で提示して承認を得る:
   ```
   Sprint N プラン

   | Wave | # | タスク | スキル | 依存 |
   |------|---|--------|--------|------|
   | 1 | b1 | [action] | [skill] | - |
   | 1 | b2 | [action] | [skill] | - |
   | 2 | b3 | [action] | [skill] | b1 |
   | 2 | b4 | [action] | - | b2 |
   | 3 | b5 | [action] | [skill] | b3, b4 |

   並列実行: Wave 1 (2件同時) → Wave 2 (2件同時) → Wave 3 (1件)

   今回反映した改善点:
   - [next_sprint_action 1]
   - [next_sprint_action 2]

   このプランで進めますか？
   ```

## ゲート条件（Phase 5 に進む前に確認）

- [ ] ユーザーがスプリントプランを承認した
- [ ] `plan.json` の `sprints[]` と `execution_groups` が更新されている

→ 条件を満たしたら **Phase 5: タスク実行** へ進む
