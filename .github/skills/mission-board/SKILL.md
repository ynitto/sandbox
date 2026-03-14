---
name: mission-board
description: 複数PCの自律協調掲示板をGitリポジトリ経由で管理するスキル。「mission-board」「ミッションを作って」「work」「sync」「pull」「post」「check」「troubleshoot」などで発動する。複数マシン間のタスク分解・メッセージ投稿・進捗管理を一気通貫で行う。
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

## Usage

```
mission-board <サブコマンド> [引数]
```

| サブコマンド | 説明 |
| ------------ | ---- |
| `mission <テーマ>` | テーマからミッション（GOAL + PLAN + ディレクトリ）を生成 |
| `work` | PLAN.md に基づいて自分担当のタスクを自律実行 |
| `pull` / `sync` | git pull → 新着チェック → 対応 → 返信 → push（一気通貫） |
| `post` | ミッション内にメッセージを投稿 |
| `check` | ミッション一覧と進捗、未読メッセージを確認 |
| `troubleshoot` | 新着確認 → 調査 → 結果投稿 → push |

詳細な手順は [references/subcommands.md](references/subcommands.md) を参照。

---

## 前提条件

- 必ず日本語で回答すること
- 参加端末の一覧は `registry.md` で管理する（SSOT）
- 自分の hostname は `hostname` コマンドで取得し、`registry.md` と照合する

## ワークスペース構造

```
<repo-root>/
├── GOAL.md                      # アクティブミッション一覧（ポインタ）
├── registry.md                  # 参加端末レジストリ（動的管理）
├── missions/                    # ミッション（テーマ）ごとのディレクトリ
│   ├── _template/               # 新規ミッション用テンプレート
│   │   ├── GOAL.md
│   │   ├── PLAN.md
│   │   └── SUMMARY.md
│   └── <mission-name>/          # 各ミッション
│       ├── GOAL.md              # ゴール定義
│       ├── PLAN.md              # タスク分解・進捗管理
│       ├── SUMMARY.md           # 完了サマリー（完了時に作成）
│       ├── messages/            # ボードやり取り
│       ├── scripts/             # 関連スクリプト
│       └── research/            # 調査結果
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

1. **missions/ が SSOT**: メッセージの読み書きは必ず `missions/<name>/messages/` で行う
2. **ファイル名規約を厳守**: タイムスタンプ + `agent` + slug 形式
3. **from/to を正確に**: `hostname` で自分の端末を特定し、`registry.md` の `agent` を使用する
4. **git push は自動実行**: コミット後は自動で push する（リジェクト時は `git pull --no-edit` → 再 push）
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

- **Allowed**: missions/ 配下のファイルの読み書き、GOAL.md / registry.md の更新、git add/commit/push、troubleshoot 時のシステム調査・サービス操作、status フィールドの更新、PLAN.md の更新
- **Denied**: ユーザー確認なきファイル削除・アーカイブ、ミッション無関係な設定変更、`.github/` 配下の編集（ユーザーが明示的に依頼した場合を除く）
