---
name: requirements-definer
description: クラウドシステム/Webアプリの要件・スコープ・受け入れ条件を定義する。「要件定義して」「やりたいことを整理して」「要件をまとめて」「受け入れ条件を定義して」「何を作るか決めたい」などのリクエストで発動する。
---

# requirements-definer

クラウドシステム/Webアプリ向けに要件定義をまとめる。
やりたいことを要件・スコープ・受け入れ条件へ落とし込み、合意可能な形で提示する。

## ワークフロー

### Step 1: 前提を確認する

対象ユーザー、利用シーン、対象範囲（Web/クラウド）をユーザーに確認する。

> **確認例**: 「このTODOアプリは誰が使いますか？個人利用ですか、チーム共有ですか？」

### Step 2: やりたいことを要件に分解する

機能要件と非機能要件に分けて整理する。

**機能要件の例:**

| # | 要件名 | 内容 |
|---|--------|------|
| F-01 | TODO作成 | タイトル・期限・優先度を指定してTODOを登録できる |
| F-02 | ステータス管理 | TODO/進行中/完了の3ステータスを切り替えられる |
| F-03 | 一覧表示 | ステータス・優先度でフィルタリングして一覧表示できる |

**非機能要件の例:**

| # | 要件名 | 内容 |
|---|--------|------|
| N-01 | レスポンス | API応答は95パーセンタイルで500ms以内 |
| N-02 | 可用性 | 月次稼働率99.5%以上 |

### Step 3: スコープを確定する

In/Out を明記して合意する。

**スコープ表の例:**

| 機能 | In/Out | 備考 |
|------|--------|------|
| TODO CRUD | In | 基本4操作すべて |
| チーム共有・権限管理 | Out | v2以降で検討 |
| モバイルアプリ | Out | Webのみ対応 |
| メール通知 | Out | 今回は対象外 |

### Step 4: 受け入れ条件を定義する

Given/When/Then 形式で各要件に対して列挙する。

**受け入れ条件の例 (F-01):**

```
Given: ユーザーがログイン済みである
When:  タイトル「買い物」・期限「2025-03-01」・優先度「高」を入力して送信する
Then:  TODOが一覧に追加され、ステータスが「TODO」で表示される
       AND タイトルが空の場合はバリデーションエラーが表示される
```

### Step 5: 成果物として提示する

上記の内容を以下の形式でまとめてユーザーに提示し、合意を取る:

1. **要件整理表** — 機能要件・非機能要件の一覧
2. **スコープ表** — In/Out と備考
3. **受け入れ条件一覧** — 各要件の Given/When/Then

## 実行後フィードバック（必須）

> **scrum-master 経由の場合**: このセクションをスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

スキルの手順を全て完了したら、ユーザーに確認する:

「requirements-definer の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
```
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'requirements-definer','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"
```

スクリプトの出力に「EVAL_RECOMMEND: promote」または「EVAL_RECOMMEND: refine」が含まれる場合は、
skill-evaluator サブエージェントを起動して評価・昇格フローを進める:
```
skill-evaluator スキルで requirements-definer を評価する。
手順: まず .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
対象スキル: requirements-definer
```
