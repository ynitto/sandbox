# Mission Board — サブコマンド詳細手順

## 目次

- [共通手順](#共通手順)
- [mission \<テーマ\> \[タスクリスト\]](#サブコマンド-mission-テーマ-タスクリスト)
- [work](#サブコマンド-work)
- [pull / sync](#サブコマンド-pull--sync)
- [post](#サブコマンド-post)
- [check](#サブコマンド-check)
- [troubleshoot](#サブコマンド-troubleshoot)
- [close \<ミッション名\>](#サブコマンド-close-ミッション名)

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

# サブコマンド: `mission <テーマ> [タスクリスト]`

**コア機能: タスクを参加端末の capabilities に基づいて割り当て、移譲メッセージを投稿する。**

タスクリストが渡された場合はそのまま割り当てに進む。テーマのみの場合はタスク分解を任意で実施してから進む。

### Step 1: Worktree Setup & Preflight & Pull

共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal）を実行。

### Step 2: 入力の受け取り

ユーザーの入力を以下のどちらかに分類する:

- **タスクリストあり** → Step 3 へ（タスク分解をスキップ）
- **テーマのみ** → Step 2.5 へ

### Step 2.5: タスク分解（オプション・テーマのみの場合）

ユーザーにタスク分解が必要か確認する。必要な場合は `deep-research` スキルを使用する（利用不可時は自分の知識で分解し、その旨を通知）。不要な場合はユーザーにタスクリストを入力してもらう。

調査観点（deep-research 使用時）:
1. このテーマで必要な作業の全体像
2. 作業の依存関係と推奨順序
3. 想定されるリスクと回避策

### Step 3: 参加端末の確認と割り当て

`$WORKTREE_PATH/registry.md` を読み込み、active 端末と capabilities を確認する。各タスクに最適な端末を割り当てる:

| 割り当てルール | 説明 |
| -------------- | ---- |
| capabilities 一致 | タスクに必要なスキル（`shell`, `browser`, `gpu` 等）と端末の capabilities を照合 |
| 並行最大化 | 依存関係のないタスクは異なる端末に分散する |
| `@any` の活用 | どの端末でも実行できるタスクは `@any` にして先着引き取りにする |
| 自端末を優先 | capabilities が一致する場合、自端末のタスクを増やしすぎない（他端末を活用する） |

### Step 4: ミッション生成

1. `$SKILL_DIR/templates/` の GOAL.md・PLAN.md を参考に `$WORKTREE_PATH/missions/<slug>/` を作成
2. **slug**: 英数字・ハイフンのみ、テーマを表す短い名前
3. GOAL.md を作成（最終ゴール、検証コマンド/成功基準、コンテキスト）
4. PLAN.md を作成（完了条件、割り当て済みタスク分解テーブル、各タスクの詳細とコマンド）

### Step 5: GOAL.md 更新

`$WORKTREE_PATH/GOAL.md` のアクティブミッション一覧に新規ミッションを追加。

### Step 6: 各端末への移譲メッセージ投稿

自端末以外に割り当てたタスクごとに `$WORKTREE_PATH/missions/<slug>/messages/` にメッセージを投稿する。メッセージにはコピペ可能なコマンドを含める。

### Step 7: 自端末タスクの即時実行

自分担当のタスクで依存なしのものがあれば即座に実行。状態を更新し結果を記録。

### Step 8: Commit & Push

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

`deep-research` スキルで包括的な RCA 調査を実施。スキルが利用できない場合は Step 3B（通常対応）に fallback し、その旨を返信メッセージに明記する。

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

アクティブミッションの一覧・進捗・未読メッセージを表示。**read-only**（commit/push なし）。

### 手順

1. 共通手順（Worktree Setup → Preflight & Pull）を実行。Heartbeat は実行しない。
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

初見の問題、複数の原因仮説、公式ドキュメント確認が必要な場合は `deep-research` スキルを使用。スキルが利用できない場合は Step 3（通常調査）に進み、その旨を結果メッセージに明記する。

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

調査コマンドの詳細は `$SKILL_DIR/references/troubleshoot-patterns.md` を参照する。プロジェクト固有の診断手順が必要な場合はこのファイルを差し替える。

### Step 4: 調査結果投稿

`$WORKTREE_PATH/missions/<name>/messages/` にトラブルシューティング結果を投稿（テーブル形式サマリ、分析、依頼事項）。

### Step 5: 元メッセージの status を `done` に更新

### Step 6: Commit & Push

共通手順: Commit & Push（コミットメッセージ: `feat: troubleshoot <slug> and post results`）を実行。

---

# サブコマンド: `close <ミッション名>`

ミッションを完了扱いにし、GOAL.md のアクティブリストから除去する。

### 手順

1. 共通手順（Worktree Setup → Preflight & Pull → Heartbeat → Goal）を実行
2. 対象ミッションの `PLAN.md` で未完了タスク（`todo`/`doing`）がないか確認。残存する場合はユーザーに確認してから続行
3. `$SKILL_DIR/templates/SUMMARY.md` を参考に `$WORKTREE_PATH/missions/<name>/SUMMARY.md` を作成:
   - 結果ステータス（✅ Complete / ❌ Unresolved / ⚠️ Partial）
   - 実施内容（時系列）
   - 根本原因・学び・再発防止策
4. `$WORKTREE_PATH/GOAL.md` のアクティブミッション一覧から対象ミッションを除去（またはアーカイブ済みとしてマーク）
5. 共通手順: Commit & Push（コミットメッセージ: `feat: close mission <name>`）を実行
