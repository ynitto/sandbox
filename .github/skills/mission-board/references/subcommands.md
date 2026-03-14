# Mission Board — サブコマンド詳細手順

## 目次

- [共通手順](#共通手順)
- [mission \<テーマ\>](#サブコマンド-mission-テーマ)
- [work](#サブコマンド-work)
- [pull / sync](#サブコマンド-pull--sync)
- [post](#サブコマンド-post)
- [check](#サブコマンド-check)
- [troubleshoot](#サブコマンド-troubleshoot)

---

# 共通手順

## Preflight & Pull

1. `git status --porcelain` でクリーンか確認。出力あれば `git stash push -u -m "autostash before pull"`
2. `git branch --show-current` でブランチ確認。原則 `master` 以外なら STOP
3. `git remote get-url origin` で origin 確認。失敗なら STOP
4. `git pull` 実行。コンフリクト時は stash/pull/pop で解決を試みる
5. Autostash していた場合は `git stash pop` で復元

## Heartbeat — 端末レジストリ更新（MANDATORY）

1. `hostname` で自分の端末名を取得
2. `registry.md` で自分を `last-seen` = 現在時刻、`status` = `🟢 active` に更新
3. 未登録なら新規行を追加
4. 他端末: 30分超過 → `🟡 idle`、2時間超過 → `🔴 offline`
5. offline 端末の未完了タスクは `@any` に再割り当て

## Goal — アクティブミッション表示（MANDATORY）

1. `GOAL.md` を読み込み、アクティブミッション一覧を「🎯 アクティブミッション」として表示
2. 各ミッションの PLAN.md の進捗概要を表示

## 新着メッセージ確認

1. `missions/*/messages/*.md` を読み込み
2. 自分宛 (`to: all` / 自分の agent / hostname) かつ `status: unread` を抽出
3. priority 順に表示
4. 0件の場合: todo タスクがあれば `work` フローへ、なければ終了

---

# サブコマンド: `mission <テーマ>`

テーマを受け取って、ミッション（ディレクトリ + GOAL.md + PLAN.md）を自動生成し、自分側の初期タスクがあれば即実行する。

### Step 1: テーマの受け取り

ユーザーの入力からミッションのテーマを特定する。

### Step 2: 参加端末の確認

`registry.md` を読み込み、active 端末の一覧と capabilities を確認する。

- 端末が 2台のみ：直接 `@hostname` でアサイン
- 端末が 3台以上：`@any` を活用して並行実行を最大化

### Step 3: DeepResearch で背景調査（推奨）

即座に分解できるシンプルなタスクでなければ `deep-research` スキルを使用する。

調査観点:
1. このテーマで必要な作業の全体像
2. 作業の依存関係と推奨順序
3. 想定されるリスクと回避策
4. 各ステップの具体的なコマンド

### Step 4: ミッション生成

1. `missions/_template/` の内容を参考に `missions/<slug>/` を作成
2. **slug**: 英数字・ハイフンのみ、テーマを表す短い名前
3. GOAL.md を作成（最終ゴール、検証コマンド/成功基準、コンテキスト、ステータステーブル）
4. PLAN.md を作成（完了条件、タスク分解テーブル、各タスクの詳細）

### Step 5: GOAL.md 更新

`GOAL.md` のアクティブミッション一覧に新規ミッションを追加。

### Step 6: 初期タスク実行

自分担当のタスクで依存なしのものがあれば即座に実行。状態を更新し結果を記録。

### Step 7: 相手への依頼

相手担当のタスクがある場合、`messages/` にメッセージを投稿（コピペ可能なコマンド付き）。

### Step 8: Commit & Push

```
git add missions/ GOAL.md
git commit -m "feat: create mission <slug>"
git push origin <branch>
```

---

# サブコマンド: `work`

アクティブミッションの PLAN.md を読み、自分担当のタスクを依存順に実行する。

### Step 1: Pull & Goal & Registry

1. 共通手順: Preflight & Pull を実行
2. `GOAL.md` を読んでアクティブミッション一覧を確認・表示
3. `registry.md` を読んで参加端末一覧を確認
4. `hostname` で自分の端末名を取得し、registry の `last-seen` を更新

### Step 2: PLAN.md 読み込み

各アクティブミッションの PLAN.md を読み込み、以下を抽出:

- **自分担当 + 状態が `todo`** のタスク
- **`@any` で未引き取り + 状態が `todo`** のタスク → 自分が引き取る
- 依存関係（依存タスクが完了していないタスクはスキップ）

### Step 3: 未読メッセージ確認

`messages/` 内の未読メッセージ（自分宛）を確認。あれば内容を読んで PLAN.md に反映。

### Step 4: タスク実行

1. `🔲 todo` → `🔄 doing` に更新
2. タスク詳細に従って実行
3. 結果を記録（✅/❌/⚠️）
4. 状態を `✅ done` or `❌ failed` に更新

### Step 5: 結果評価

- **全条件 OK** → ミッション完了、GOAL.md 更新、完了メッセージ投稿
- **条件未達** → 失敗分析、代替タスク追加、相手への依頼

### Step 6: 結果メッセージ投稿

実行結果を `messages/` にメッセージとして投稿。

### Step 7: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: work on <mission> tasks <N1,N2,...>"
git push origin <branch>
```

---

# サブコマンド: `pull` / `sync`

git pull → 新着チェック → 対応 → 返信 → push（一気通貫）。

### Step 1-2: Pull & Check

共通手順（Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

### Step 2.5: Routing — RCA 調査が必要か判定（MANDATORY）

**即時 DR 条件**（1つでも該当 → Step 3A 確定）:

| 条件 | 例 |
| ---- | -- |
| priority が `urgent` で技術的問題解決を求めている | 緊急ネットワーク障害 |
| 本文に「原因を調べて」「調査して」「なぜ〜」等の調査依頼表現がある | — |

**蓄積 DR 条件**（同一トピックで 5件累積 → Step 3A）:

| 条件 |
| ---- |
| tags に `RCA`, `調査依頼`, `トラブルシューティング`, `障害` を含む |
| priority が `high` で技術的問題解決を求めている |
| 未解決の技術的問題に関するメッセージが累積 |

蓄積カウント: 直近の DR 実施地点（tags に `DeepResearch` を含む最新メッセージ）でリセット。

**判定結果を必ず表示:**

```
| メッセージ | 即時DR条件 | 蓄積DR条件 | 累積N/5 | 判定 |
|-----------|-----------|-----------|---------|------|
```

### Step 3A: DeepResearch で RCA 調査

`deep-research` スキルで包括的な RCA 調査を実施。Step 3A 判定後のスキップは不可。

調査観点:
1. 根本原因の特定（5 Whys 分析を含む）
2. 類似事例の調査
3. 解決策と代替手段の網羅的調査
4. 再発防止策の提案

出力形式: TL;DR + 根本原因分析（テーブル）+ 解決策（優先順位付き、コマンド付き）+ 参考文献

### Step 3B: 通常の包括的対応

未読メッセージごとに priority 順で対応。SKILL.md の「最小往復・最大自己解決の原則」と「返信前チェックリスト」に従う。

### Step 4: 返信メッセージの投稿

元メッセージと同じミッションの `messages/` に返信を作成。Step 3A 実施時は tags に `DeepResearch` を必ず追加。

### Step 5: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: respond to <slug>"
git push origin <branch>
```

### Step 6: 新着なしの場合

todo タスクがあれば `work` フローに自動移行。なければ終了。

---

# サブコマンド: `post`

アクティブミッション内に新しいメッセージを投稿。

### 手順

1. ミッション、宛先(to)、優先度(priority)、タグ(tags)、本文を確認
2. `GOAL.md` からアクティブミッションを確認
3. `messages/` ディレクトリにメッセージを作成
4. git commit & push まで自動実行

---

# サブコマンド: `check`

アクティブミッションの一覧・進捗・未読メッセージを表示。

### 出力フォーマット

**ミッション一覧:**

| ミッション | ディレクトリ | 状態 | 進捗 |
| ---------- | ------------ | ---- | ---- |

**タスク進捗（ミッションごと）:**

| # | タスク | 担当 | 状態 | 結果 |
| - | ------ | ---- | ---- | ---- |

**未読メッセージ:**

| ファイル | From | Priority | Status | Created | タイトル |
| -------- | ---- | -------- | ------ | ------- | -------- |

---

# サブコマンド: `troubleshoot`

新着メッセージの問題に対してトラブルシューティングを実施し、結果を投稿。

### Step 1-2: Pull & Check

共通手順（Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

### Step 2.5: DeepResearch で事前調査（推奨）

初見の問題、複数の原因仮説、公式ドキュメント確認が必要な場合は `deep-research` スキルを使用。

### Step 3: 調査・診断の実施

#### 調査の進め方

1. **問題の特定**: メッセージから問題点を抽出
2. **レイヤー順に深掘り**: 疎通 → ポート → サービス → アプリ
3. **全仮説を並行検証**
4. **各ステップの結果を記録**
5. **見つけた問題は即修正**
6. **根本原因の特定**: 確定を目指す
7. **代替手段の事前検証**
8. **相手への依頼事項を整理**: コピペ可能なコマンド付き

#### 調査深度の基準

| 層 | 内容 | コマンド例 |
| -- | ---- | ---------- |
| 第1層 | 高レベルAPI | `Get-Service`, `Test-NetConnection` |
| 第2層 | レジストリ・設定 | `Get-ItemProperty`, `Get-SmbServerConfiguration` |
| 第3層 | ドライバー・カーネル | `sc.exe query` |
| 第4層 | プロトコルレベル | RAW TCP socket |

#### 典型パターン

| カテゴリ | 確認コマンド例 |
| -------- | -------------- |
| 疎通 | `ping`, `Test-NetConnection` |
| ポート | `Test-NetConnection -Port <n>` |
| DNS | `nslookup`, `nbtstat -A` |
| SMB | `net view`, `net use`, `Get-SmbShare` |
| サービス | `Get-Service`, `sc.exe query` |
| FW | `Get-NetFirewallRule` |
| ログ | `Get-EventLog`, `Get-WinEvent` |

権限不足時は代替コマンドに切り替える。同じコマンドを繰り返さない。

### Step 4: 調査結果投稿

`messages/` にトラブルシューティング結果を投稿（テーブル形式サマリ、分析、依頼事項）。

### Step 5: 元メッセージの status を `done` に更新

### Step 6: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: troubleshoot <slug> and post results"
git push origin <branch>
```
