gitlab-idd ワーカーとして、以下の GitLab イシューを担当・実行してください。
このタスクは WSL2 環境内で kiro が実行します。

## WSL2 環境セットアップ

まず以下のコマンドを実行してリポジトリを WSL 内にクローンしてください。

```bash
# GitLab Personal Access Token を設定（未設定の場合）
# ~/.bashrc または ~/.zshrc に追記して永続化することを推奨
export GITLAB_TOKEN="<Your GitLab Personal Access Token>"

# リポジトリを WSL 内にクローン
mkdir -p /tmp/gitlab-idd-work
cd /tmp/gitlab-idd-work
git clone https://${host}/${project}.git
cd ${project_name}
```

> 注意: `GITLAB_TOKEN` が未設定の場合、`gl.py` によるイシュー操作（assign・ラベル更新・コメント投稿）が失敗します。

---

## イシュー情報

| 項目 | 内容 |
|------|------|
| イシュー ID | #${issue_id} |
| タイトル | ${issue_title} |
| URL | ${issue_url} |
| プロジェクト | ${host}/${project} |
| クローン先 | `${clone_dir}` |
| 推奨ブランチ名 | `${branch_name}` |
| ラベル | ${issue_labels} |

## イシュー本文

${issue_body}

---

## 実行指示

SKILL.md の「ワーカー — イシュー取得・実行・報告」フローに従い、WSL 内クローンしたリポジトリを作業ディレクトリとして、以下を一気通貫で実行してください。

1. クローンしたリポジトリ内で `python scripts/gl.py` が動作することを確認
2. イシュー #${issue_id} を自分に assign してロック（競合防止）
3. `${branch_name}` ブランチを作成
4. 受け入れ条件をすべて満たす実装を行う
5. 並列評価ループ（機能・セキュリティ・アーキテクチャ）で品質を確認
6. ブランチを push して Draft MR を作成（本文に `Closes #${issue_id}` を含める）
7. イシューに完了コメントを投稿し `status:review-ready` ラベルを設定
