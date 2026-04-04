gitlab-idd ワーカーとして、以下の GitLab イシューを担当・実行してください。

## イシュー情報

| 項目 | 内容 |
|------|------|
| イシュー ID | #${issue_id} |
| タイトル | ${issue_title} |
| URL | ${issue_url} |
| プロジェクト | ${host}/${project} |
| ローカルパス | ${local_path} |
| 推奨ブランチ名 | `${branch_name}` |
| ラベル | ${issue_labels} |

## イシュー本文

${issue_body}

---

## 実行指示

SKILL.md の「ワーカー — イシュー取得・実行・報告」フローに従い、以下を一気通貫で実行してください。

1. イシュー本文の `## 依存イシュー` セクションを確認し、記載されたイシューがすべて完了済みか検証する
   - 未完了の依存イシューがある場合はコメントを投稿して終了する
2. イシュー #${issue_id} を自分に assign してロック（競合防止）
3. `${branch_name}` ブランチを作成
4. 受け入れ条件をすべて満たす実装を行う
5. skill-selector の出力契約に従い、`primary_skills` / `supporting_skills` / `execution_plan` を構造のまま扱う
6. `supporting_skills` は `mode` / `timing` / `name` / `instruction` に従ってそのまま適用する
7. レビューは常に `agent-reviewer` に委譲する。perspective の決定と並列レビューは `agent-reviewer` に任せる
8. ブランチを push して Draft MR を作成（本文に `Closes #${issue_id}` を含める）
9. イシューに完了コメントを投稿し `status:review-ready` ラベルを設定
