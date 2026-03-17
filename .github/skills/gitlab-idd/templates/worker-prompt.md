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

1. イシュー #${issue_id} を自分に assign してロック（競合防止）
2. `${branch_name}` ブランチを作成
3. 受け入れ条件をすべて満たす実装を行う
4. 並列評価ループ（機能・セキュリティ・アーキテクチャ）で品質を確認
5. ブランチを push して Draft MR を作成（本文に `Closes #${issue_id}` を含める）
6. イシューに完了コメントを投稿し `status:review-ready` ラベルを設定
