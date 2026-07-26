---
name: backlog-planner
description: agent-project の charter（プロジェクト憲章）と観点メモを、人がレビューできる粒度のバックログへ分解するプランナー向けスキル。各タスクに why・作業概要・受入基準チェックリスト（acceptance）・規模感を必ず書かせ、既存タスクと墓標を入力に取って重複を出さない。agent-project の plan から呼ばれる。
metadata:
  version: 1.0.0
  tier: experimental
  category: planning
  tags:
    - agent-project
    - backlog
    - planning
    - acceptance-criteria
---

# backlog-planner — 「エージェントが書き、人が直す」バックログ

## 概要

charter（＋観点メモ）を、**人がタスクグラフ作成前にレビューできる粒度**のバックログへ分解する。

**なぜ人が読める粒度が要るのか**: 従来の分解は `title` と `verify`（1 行のシェルコマンド）しか
出さなかった。人はそれを見ても「このタスクが何をするのか」「なぜ要るのか」「どこを触るのか」が
分からず、計画レビューが実質的に機能しない。判断できないものは承認するしかない。

このスキルは、レビューに要る材料をタスク自身に持たせる:
**why（なぜ）・作業概要（何をどこまで）・受入基準（何をもって完了とするか）・規模感。**

受入基準（`acceptance`）は `backlog-verifier` が settle 時に証跡付きで判定する一次表現である
（書式の正典は `tools/agent-project/backlog.md.example`）。**ここで書かれたものが、そのまま
done の根拠になる。**

## 入出力契約

`scripts/prompt.py` は **プロンプトを組み立てるだけ**（LLM は呼ばない）。実行・予算管理・
失敗トリアージは agent-project 側が持つ（`backlog-verifier` と同じ形）。

```
echo '<入力 JSON>' | python3 scripts/prompt.py
→ プロンプト本文を stdout に出力
```

### 入力 JSON

| キー | 内容 |
|---|---|
| `charter` | 憲章の本文（目標・制約・前提・成果物・受入条件・利用可能なリポジトリ） |
| `owns` | どの repo がどのパスを担当するか（書込先 workspace 選定の根拠） |
| `granularity` | `coarse`（既定・ユーザーストーリー相当） / `fine` / `finest` |
| `rules` | `rules.md`（プロジェクト恒常ルール）の抜粋 |
| `repo_context` | `context/<repo>.md`（repo-map）の抜粋。**作業概要の「変更対象」はこれを根拠に書く** |
| `existing` | 既存タスク `[{id, title, status, edited, summary}]`。`edited=="human"` は人が確定させたもの |
| `tombstones` | 墓標 `[{title, reason}]`（人が却下・削除したタスク） |
| `notes` | 観点メモの本文（`distill-notes` のときのみ） |
| `retry` | 前回出力の欠落セクション（再要求時のみ） |

### 出力

タスク spec の **JSON 配列のみ**。各要素:

| キー | 必須 | 内容 |
|---|---|---|
| `title` | ● | タスクの題 |
| `why` | ● | charter のどの目標に効くか（1〜2 文） |
| `desc` | ● | **作業概要**: 変更対象（リポジトリと主要ファイル/モジュールの見込み）・作業ステップ 3〜7 行・影響範囲 |
| `acceptance` | ● | **受入基準の配列**（自然文 3〜7 項目） |
| `size` | ● | `S` / `M` / `L` |
| `workspace` | ● | 唯一の書込先 repo 名（`owns` を持つもの） |
| `refs` | | 読むだけの参照 repo |
| `scope` / `out_of_scope` / `hints` | | 触ってよい範囲 / やらないこと / 実装の手がかり |
| `after` | | 先行タスクの `title`（配列内・循環不可） |
| `verify` | | 書けるなら決定的シェルコマンド（**書けないなら省く**。無理に書かせない） |
| `cohort_items` | | 同じ手順を多対象に繰り返すときの対象一覧（`{item}` 展開） |

## 不変条件（agent-project 側が機械的に強制する）

1. **必須セクション欠落は 1 回だけ再要求** → なお欠落なら `status: draft` で投入し、
   欠落項目を票に書く。**捨てない**（沈黙で落とすと、charter が悪いのかスキルが壊れたのか
   人が切り分けられない）
2. **墓標（完全一致）は投入されない**。類似は投入されるが needs に注記が付く
3. **既存タスクとの重複は投入側でも Jaccard 照合で弾かれる**（スキルは差し替え可能なので、
   投入側の護りは外さない）
4. `edited: human` のタスクは**再提案しない**（人の記述 > エージェント提案）

## カスタマイズ

上位のスキル置き場（プロジェクトの `.github/skills/backlog-planner/`）に同名スキルを置けば
全面的に差し替えられる。設定 `planner_skill` でスキル名自体も変えられる。
スキルが見つからないときは agent-project の組み込みプロンプト（同じ出力契約）へ落ちる
——計画が止まるとプロジェクトが 1 歩も進まないので、スキルは必須にしない。
