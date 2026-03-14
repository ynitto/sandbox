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

## 変数定義

以下の変数を全手順で使用する。

**Linux / macOS（sh/bash）:**

```sh
MISSIONS_BRANCH=missions
WORKTREE_PATH=.worktrees/missions
# SKILL_DIR: ワークスペースの .github/ を優先し、なければユーザーホームの ~/.copilot/ を使用
if [ -d ".github/skills/mission-board" ]; then
  SKILL_DIR=".github/skills/mission-board"
elif [ -d "$HOME/.copilot/skills/mission-board" ]; then
  SKILL_DIR="$HOME/.copilot/skills/mission-board"
else
  SKILL_DIR=".github/skills/mission-board"
fi
```

**Windows（PowerShell）:**

```powershell
$MISSIONS_BRANCH = "missions"
$WORKTREE_PATH = ".worktrees/missions"
# SKILL_DIR: ワークスペースの .github/ を優先し、なければユーザーホームの ~/.copilot/ を使用
if (Test-Path ".github/skills/mission-board") {
  $SKILL_DIR = ".github/skills/mission-board"
} elseif (Test-Path "$env:USERPROFILE/.copilot/skills/mission-board") {
  $SKILL_DIR = "$env:USERPROFILE/.copilot/skills/mission-board"
} else {
  $SKILL_DIR = ".github/skills/mission-board"
}
```

> **Note**: Windows cmd では `%MISSIONS_BRANCH%` 形式の変数参照になるが、Claude Code on Windows は PowerShell を使用するため PowerShell 構文を優先する。

## Worktree Setup — ミッションボード worktree のセットアップ（MANDATORY）

全サブコマンドの最初に実行する。

1. worktree が存在するか確認:
   ```sh
   git worktree list
   ```
2. `$WORKTREE_PATH` がリストにない場合、`missions` ブランチの存在を確認:
   ```sh
   git ls-remote --heads origin $MISSIONS_BRANCH
   ```
3. **リモートに存在する**: worktree を追加してチェックアウト:
   ```sh
   git worktree add $WORKTREE_PATH $MISSIONS_BRANCH
   ```
4. **リモートに存在しない**: 空の孤立ブランチを作成して worktree を追加:
   ```sh
   git worktree add --orphan -b $MISSIONS_BRANCH $WORKTREE_PATH
   ```
   その後、ボードの初期ファイルを作成:
   ```sh
   # $WORKTREE_PATH/GOAL.md を作成（GOAL.md のテンプレート内容で）
   # $WORKTREE_PATH/registry.md を作成（hostname と自端末情報を記載）
   cd $WORKTREE_PATH
   git add GOAL.md registry.md
   git commit -m "chore: initialize mission board"
   git push -u origin $MISSIONS_BRANCH
   ```

## Preflight & Pull

1. `git -C $WORKTREE_PATH status --porcelain` でクリーンか確認。出力あれば `git -C $WORKTREE_PATH stash push -u -m "autostash before pull"`
2. `git -C $WORKTREE_PATH pull origin $MISSIONS_BRANCH` 実行。コンフリクト時は stash/pull/pop で解決を試みる
3. Autostash していた場合は `git -C $WORKTREE_PATH stash pop` で復元

## Heartbeat — 端末レジストリ更新（MANDATORY）

1. `hostname` で自分の端末名を取得
2. `$WORKTREE_PATH/registry.md` で自分を `last-seen` = 現在時刻、`status` = `🟢 active` に更新
3. 未登録なら新規行を追加

> **Note**: 他端末のステータス変更（idle/offline への更新）や未完了タスクの再割り当ては `check` コマンドでユーザーが明示的に確認した上で行う。自動変更は競合コミットの原因になるため禁止。

## Goal — アクティブミッション表示（MANDATORY）

1. `$WORKTREE_PATH/GOAL.md` を読み込み、アクティブミッション一覧を「🎯 アクティブミッション」として表示
2. 各ミッションの PLAN.md の進捗概要を表示

## 新着メッセージ確認

1. `$WORKTREE_PATH/missions/*/messages/*.md` を読み込み
2. 自分宛 (`to: all` / 自分の agent / hostname) かつ `status: unread` を抽出
3. priority 順に表示
4. 0件の場合: todo タスクがあれば `work` フローへ、なければ終了

## Commit & Push（worktree 内で実行）

```sh
cd $WORKTREE_PATH
git add missions/ GOAL.md registry.md
git commit -m "<コミットメッセージ>"
git push origin $MISSIONS_BRANCH
```

---

# サブコマンド: `mission <テーマ>`

テーマを受け取って、ミッション（ディレクトリ + GOAL.md + PLAN.md）を自動生成し、自分側の初期タスクがあれば即実行する。

### Step 1: Worktree Setup & Preflight & Pull

共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal）を実行。

### Step 2: テーマの受け取り

ユーザーの入力からミッションのテーマを特定する。

### Step 3: 参加端末の確認

`$WORKTREE_PATH/registry.md` を読み込み、active 端末の一覧と capabilities を確認する。

- 端末が 2台のみ：直接 `@hostname` でアサイン
- 端末が 3台以上：`@any` を活用して並行実行を最大化

### Step 4: DeepResearch で背景調査（推奨）

即座に分解できるシンプルなタスクでなければ `deep-research` スキルを使用する。

調査観点:
1. このテーマで必要な作業の全体像
2. 作業の依存関係と推奨順序
3. 想定されるリスクと回避策
4. 各ステップの具体的なコマンド

### Step 5: ミッション生成

1. `$SKILL_DIR/templates/` の GOAL.md・PLAN.md を参考に `$WORKTREE_PATH/missions/<slug>/` を作成
2. **slug**: 英数字・ハイフンのみ、テーマを表す短い名前
3. GOAL.md を作成（最終ゴール、検証コマンド/成功基準、コンテキスト、ステータステーブル）
4. PLAN.md を作成（完了条件、タスク分解テーブル、各タスクの詳細）

### Step 6: GOAL.md 更新

`$WORKTREE_PATH/GOAL.md` のアクティブミッション一覧に新規ミッションを追加。

### Step 7: 初期タスク実行

自分担当のタスクで依存なしのものがあれば即座に実行。状態を更新し結果を記録。

### Step 8: 相手への依頼

相手担当のタスクがある場合、`$WORKTREE_PATH/missions/<slug>/messages/` にメッセージを投稿（コピペ可能なコマンド付き）。

### Step 9: Commit & Push

共通手順: Commit & Push（コミットメッセージ: `feat: create mission <slug>`）を実行。

---

# サブコマンド: `work`

アクティブミッションの PLAN.md を読み、自分担当のタスクを依存順に実行する。

### Step 1: Worktree Setup & Pull & Goal & Registry

1. 共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal）を実行
2. `$WORKTREE_PATH/registry.md` を読んで参加端末一覧を確認

### Step 2: PLAN.md 読み込み

各アクティブミッションの `$WORKTREE_PATH/missions/<name>/PLAN.md` を読み込み、以下を抽出:

- **自分担当 + 状態が `todo`** のタスク
- **`@any` で未引き取り + 状態が `todo`** のタスク → 自分が引き取る
- 依存関係（依存タスクが完了していないタスクはスキップ）

### Step 3: 未読メッセージ確認

`$WORKTREE_PATH/missions/<name>/messages/` 内の未読メッセージ（自分宛）を確認。あれば内容を読んで PLAN.md に反映。

### Step 4: タスク実行

1. `🔲 todo` → `🔄 doing` に更新
2. タスク詳細に従って実行
3. 結果を記録（✅/❌/⚠️）
4. 状態を `✅ done` or `❌ failed` に更新

### Step 5: 結果評価

- **全条件 OK** → ミッション完了、GOAL.md 更新、完了メッセージ投稿
- **条件未達** → 失敗分析、代替タスク追加、相手への依頼

### Step 6: 結果メッセージ投稿

実行結果を `$WORKTREE_PATH/missions/<name>/messages/` にメッセージとして投稿。

### Step 7: Commit & Push

共通手順: Commit & Push（コミットメッセージ: `feat: work on <mission> tasks <N1,N2,...>`）を実行。

---

# サブコマンド: `pull` / `sync`

git pull → 新着チェック → 対応 → 返信 → push（一気通貫）。

### Step 1: Worktree Setup & Pull & Check

共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

新着メッセージが 0件の場合: todo タスクがあれば `work` フローに自動移行。なければ終了。

### Step 2: DR 判定

以下のいずれかに該当 → Step 3A（DeepResearch）へ。どちらも非該当 → Step 3B（通常対応）へ。

- priority が `urgent` かつ技術的問題解決を求めている
- 本文に「原因を調べて」「調査して」「なぜ〜」等の調査依頼表現がある

### Step 3A: DeepResearch で RCA 調査

`deep-research` スキルで包括的な RCA 調査を実施。

調査観点:
1. 根本原因の特定（5 Whys 分析を含む）
2. 類似事例の調査
3. 解決策と代替手段の網羅的調査
4. 再発防止策の提案

出力形式: TL;DR + 根本原因分析（テーブル）+ 解決策（優先順位付き、コマンド付き）+ 参考文献

### Step 3B: 通常の包括的対応

未読メッセージごとに priority 順で対応。SKILL.md の「最小往復・最大自己解決の原則」と「返信前チェックリスト」に従う。

### Step 4: 返信メッセージの投稿

元メッセージと同じミッションの `$WORKTREE_PATH/missions/<name>/messages/` に返信を作成。Step 3A 実施時は tags に `DeepResearch` を必ず追加。

### Step 5: Commit & Push

共通手順: Commit & Push（コミットメッセージ: `feat: respond to <slug>`）を実行。

---

# サブコマンド: `post`

アクティブミッション内に新しいメッセージを投稿。

### 手順

1. 共通手順（Worktree Setup → Preflight & Pull → Heartbeat）を実行
2. ミッション、宛先(to)、優先度(priority)、タグ(tags)、本文を確認
3. `$WORKTREE_PATH/missions/<name>/messages/` ディレクトリにメッセージを作成
4. 共通手順: Commit & Push（コミットメッセージ: `feat: post message to <mission>`）を実行

---

# サブコマンド: `check`

アクティブミッションの一覧・進捗・未読メッセージを表示。

### 手順

1. 共通手順（Worktree Setup → Preflight & Pull → Heartbeat）を実行
2. `$WORKTREE_PATH/GOAL.md` と各ミッションの PLAN.md・messages/ を読み込んで表示

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

### Step 1-2: Worktree Setup & Pull & Check

共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

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

| 層 | 内容 | コマンド例（Linux/macOS） | コマンド例（Windows/PowerShell） |
| -- | ---- | ------------------------- | -------------------------------- |
| 第1層 | 高レベルAPI | `systemctl status`, `nc -zv` | `Get-Service`, `Test-NetConnection` |
| 第2層 | 設定ファイル | `cat /etc/...`, `sysctl` | `Get-ItemProperty`, `Get-SmbServerConfiguration` |
| 第3層 | カーネル・ドライバー | `dmesg`, `lsmod` | `sc.exe query` |
| 第4層 | プロトコルレベル | `tcpdump`, `ss -tulnp` | RAW TCP socket |

#### 典型パターン

| カテゴリ | Linux/macOS | Windows/PowerShell |
| -------- | ----------- | ------------------ |
| 疎通 | `ping`, `traceroute` | `ping`, `Test-NetConnection` |
| ポート | `nc -zv <host> <port>`, `ss -tulnp` | `Test-NetConnection -Port <n>` |
| DNS | `dig`, `nslookup` | `nslookup`, `nbtstat -A` |
| SMB/共有 | `smbclient -L` | `net view`, `Get-SmbShare` |
| サービス | `systemctl status`, `ps aux` | `Get-Service`, `sc.exe query` |
| FW | `iptables -L`, `ufw status` | `Get-NetFirewallRule` |
| ログ | `journalctl`, `tail /var/log/...` | `Get-EventLog`, `Get-WinEvent` |

権限不足時は代替コマンドに切り替える。同じコマンドを繰り返さない。

### Step 4: 調査結果投稿

`$WORKTREE_PATH/missions/<name>/messages/` にトラブルシューティング結果を投稿（テーブル形式サマリ、分析、依頼事項）。

### Step 5: 元メッセージの status を `done` に更新

### Step 6: Commit & Push

共通手順: Commit & Push（コミットメッセージ: `feat: troubleshoot <slug> and post results`）を実行。
