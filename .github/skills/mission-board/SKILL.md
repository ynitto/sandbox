---
name: mission-board
description: 複数PCへのタスク分散移譲をGitリポジトリ経由で管理するスキル。ミッション作成・タスク割り当て・作業実行・メッセージ同期・進捗確認・トラブル調査・ミッション完了に対応。タスク分解はオプション。「ミッションを作って」「タスクを各PCに割り当てて」「担当タスクを実行して」「ボードを同期して」「ミッションに投稿して」「ボードを確認して」「問題を調査して」「ミッションを完了して」などで発動。
metadata:
  version: 2.0.0
  tier: experimental
  category: collaboration
  tags:
    - multi-agent
    - git
    - mission-management
---

# Mission Board — 複数マシン間の自律協調掲示板

複数の PC 間でのミッション管理・メッセージの投稿・確認を Git リポジトリ経由で行う掲示板管理スキル。

## 操作一覧

| 操作 | トリガー例 |
| ---- | ---------- |
| **ミッション** | 「ミッションを作って」「〇〇のタスクを各PCに割り当てて」「複数端末でタスクを始めたい」 |
| **作業** | 「担当タスクを実行して」「作業を始めて」「ボードのタスクをこなして」 |
| **同期** | 「ボードを同期して」「最新のメッセージを取得して対応して」「ミッションを更新して」 |
| **投稿** | 「ミッションにメッセージを送って」「ボードに投稿して」「PC-B に連絡して」 |
| **確認** | 「ボードを確認して」「進捗を見せて」「未読メッセージを表示して」 |
| **調査** | 「問題を調査して」「トラブルシューティングして」「エラーの原因を調べて」 |
| **完了** | 「ミッションを完了して」「〇〇ミッションを閉じて」「ミッションを締めて」 |

---

## ミッション

タスクリストを受け取り、参加端末の capabilities に基づいて割り当てて移譲メッセージを投稿する。タスクリスト省略時はオプションでタスク分解を実施してから移譲に進む。

- タスクリストあり → capabilities マッチング → 移譲メッセージ → 自端末タスク即実行
- テーマのみ → ユーザーに分解要否を確認 → 上記へ

→ 詳細: [references/subcommands.md — ミッション](references/subcommands.md)

---

## 作業

PLAN.md を読み込み、自分担当かつ依存が解決済みのタスクを順に実行する。`@any` タスクは先着引き取り。実行結果をメッセージとして投稿し、ミッション完了条件を評価する。

→ 詳細: [references/subcommands.md — 作業](references/subcommands.md)

---

## 同期

git pull → 自分宛の未読メッセージ確認 → priority 順に対応 → 返信投稿 → push を一気通貫で実行する。新着なしの場合は `作業` に自動移行。

- urgent / 調査依頼メッセージ → `deep-research` で RCA 調査（利用不可時は通常対応に fallback）
- それ以外 → 通常の包括的対応

→ 詳細: [references/subcommands.md — 同期](references/subcommands.md)

---

## 投稿

指定したミッションの `messages/` に新しいメッセージを作成して push する。worktree のセットアップ・pull・Heartbeat を実行してから投稿する。

→ 詳細: [references/subcommands.md — 投稿](references/subcommands.md)

---

## 確認

アクティブミッションの一覧・タスク進捗・未読メッセージを表示する。**read-only**（Heartbeat・commit・push なし）。

→ 詳細: [references/subcommands.md — 確認](references/subcommands.md)

---

## 調査

新着メッセージに含まれる問題をレイヤー順に診断し、結果を投稿する。`deep-research` による事前調査を推奨（利用不可時は通常調査に fallback）。調査コマンドはプロジェクト固有に差し替え可能。

→ 詳細: [references/subcommands.md — 調査](references/subcommands.md)
→ 調査コマンド集: [references/troubleshoot-patterns.md](references/troubleshoot-patterns.md)

---

## 完了

ミッションを完了扱いにする。未完了タスクがある場合はユーザーに確認してから SUMMARY.md を生成し、GOAL.md のアクティブリストから除去して push する。

→ 詳細: [references/subcommands.md — 完了](references/subcommands.md)

---

## 前提条件

- 必ず日本語で回答すること
- 参加端末の一覧は `registry.md` で管理する（SSOT）
- 自分の hostname は `hostname` コマンドで取得し、`registry.md` と照合する
- ミッションボードのブランチ名は `missions`（既定）、worktree パスは `<repo-root>/.worktrees/missions`

## ワークスペース構造

ミッションデータは専用ブランチ（`missions`）を git worktree で管理する。メインブランチは汚さない。

### スキルディレクトリ（メインブランチ または ユーザーホーム）

スキルは以下のいずれかの場所に配置できる。起動時に上から順に検索し、最初に見つかった場所を `SKILL_DIR` として使用する。

```
# ワークスペース優先（リポジトリ固有のカスタマイズ向け）
.github/skills/mission-board/

# ユーザーホームフォールバック（全リポジトリ共通設定向け）
~/.copilot/skills/mission-board/
```

どちらのディレクトリも同じ構造を持つ:

```
<SKILL_DIR>/
├── SKILL.md
├── templates/                   # 新規ミッション用テンプレート
│   ├── GOAL.md
│   ├── PLAN.md
│   └── SUMMARY.md
└── references/
    └── subcommands.md
```

### ミッションボードブランチ（worktree）

```
.worktrees/missions/             # git worktree（missions ブランチ）
├── GOAL.md                      # アクティブミッション一覧（ポインタ）
├── registry.md                  # 参加端末レジストリ（動的管理）
└── missions/                    # ミッション（テーマ）ごとのディレクトリ
    └── <mission-name>/          # 各ミッション
        ├── GOAL.md              # ゴール定義
        ├── PLAN.md              # タスク分解・進捗管理
        ├── SUMMARY.md           # 完了サマリー（完了時に作成）
        ├── messages/            # ボードやり取り
        ├── scripts/             # 関連スクリプト
        └── research/            # 調査結果
```

## メッセージ規約

- **配置場所**: メッセージは必ず該当ミッションの `messages/` ディレクトリ (`missions/<name>/messages/`) 内に作成する
- **ファイル名**: `YYYY-MM-DD_HH-MM_agent_slug.md`（agent = registry.md の `agent`）
  - 例: `missions/example-mission/messages/2026-02-22_07-00_PC-A_task-report.md`
- **slug**: 英数字・ハイフンのみ、内容がわかる短い名前
- **返信ルール**: 既存ファイルを編集せず、**新しいファイルを作成**して返信する
- **フォーマット**: 下記の YAML フロントマター + 本文

```markdown
---
from: <自分の agent>
to: <相手の agent / all>
priority: low | normal | high | urgent
status: unread | read | done
tags: [タグ1, タグ2]
created: YYYY-MM-DDTHH:MM
---

# タイトル

本文をここに書く
```

ステータス遷移: `unread` → `read`（相手が確認） → `done`（対応完了）

---

## 最小往復・最大自己解決の原則

**1回の返信で問題を解決する**ことを最優先目標とする。メッセージの往復は「コスト」であり、各往復に**数時間かかる**と想定して行動すること。

### 受信時の行動規範

返信する前に以下を全て実施する:

1. 依頼されたことをやる
2. 依頼されていないがやるべきことをやる（周辺の調査・確認・修正）
3. 修正可能なものは即座に修正する
4. 次に聞かれそうなことを先回りで調べる
5. 代替手段も先に調査・検証する
6. 自分側でできることを全て完了してから返信する

### 返信前チェックリスト

- [ ] 依頼された作業は全て実施したか？
- [ ] 見つけた問題は「報告」だけでなく「修正」まで行ったか？
- [ ] 代替案も調査・検証したか？
- [ ] 相手に依頼する内容にはコピペ可能なコマンドを添えたか？
- [ ] **この返信を受け取った相手が、追加質問なしに作業を完了できるか？**

---

## エージェント行動指針

1. **worktree が SSOT**: ミッション・メッセージの読み書きは必ず `.worktrees/missions/` 内で行う
2. **ファイル名規約を厳守**: タイムスタンプ + `agent` + slug 形式
3. **from/to を正確に**: `hostname` で自分の端末を特定し、`registry.md` の `agent` を使用する
4. **git 操作は worktree 内で実行**: commit/push は `.worktrees/missions/` ディレクトリで行い `missions` ブランチに反映する
5. **破壊的操作の前に確認**: ファイル削除・アーカイブの前にユーザーに確認する
6. **日本語で回答**: 会話はカジュアル、成果物は構造化
7. **常に一気通貫で進行**: pull → 新着確認 → 対応 → status 更新 → 返信投稿 → コミット → push の流れは途中でユーザーに確認を挟まない（破壊的操作を除く）
8. **pull 後は自動的に新着チェック**
9. **最小往復・最大自己解決**（上記参照）
10. **目的を見失わない**: `GOAL.md` でアクティブミッションを常に確認する

## コミット規約

Conventional Commits を使用: `feat:`, `fix:`, `docs:`, `chore:`

例: `feat: post network-fix instructions to PC-B`

## Permissions

- **Allowed**: `.worktrees/missions/` 配下のファイルの読み書き、worktree 内の GOAL.md / registry.md の更新、worktree 内での git add/commit/push、worktree のセットアップ（`git worktree add`）、`調査` 時のシステム調査・サービス操作、status フィールドの更新、PLAN.md の更新
- **Denied**: ユーザー確認なきファイル削除・アーカイブ、ミッション無関係な設定変更、`.github/` および `~/.copilot/` 配下のスキルファイルの編集（ユーザーが明示的に依頼した場合を除く）、メインブランチへの missions データのコミット

---

## プラットフォーム互換性

### Windows

Windows では PowerShell でコマンドを実行するため、以下の点に注意すること。

#### シェル変数構文の差異

`references/subcommands.md` の変数定義は sh/PowerShell 両対応で記載している。PowerShell では `VAR=value` の代入構文は動作しないため `$VAR = "value"` 形式を使用する。詳細は `references/subcommands.md` の「変数定義」セクションを参照。

#### パス区切り文字

Git for Windows は `/` と `\` の両方を受け入れる。本ドキュメントの `/` 区切りパスはそのまま使用できる。

#### `hostname` コマンド

Windows でも `hostname` コマンドは動作する（cmd/PowerShell 両対応）。

#### git worktree

git worktree は Windows でも動作する。詳細は `references/windows-worktree.md` を参照。

#### `調査` コマンド対応表

`references/troubleshoot-patterns.md` の「調査深度の基準」および「典型パターン」テーブルに Windows/PowerShell 向けコマンドが記載済み。
