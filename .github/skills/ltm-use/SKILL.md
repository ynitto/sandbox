---
name: ltm-use
description: セッションをまたいで知識・決定事項を継続させたいときのスキル。「覚えておいて」でsave、「思い出して」でrecall、「記憶一覧」でlist、「忘れて」でarchive、「共有して」でshare（Moltbook 公開）、「整理して」でcleanup、「役立った／間違ってた」でrate、「固定化して」でconsolidate。重要な知見を発見したら自律的にsaveを実行すること。
metadata:
  version: 5.3.3
  tier: core
  category: meta
  tags:
    - memory
    - knowledge-persistence
    - recall
    - brain-inspired
  periodic_scripts:
    - scripts/auto_update.py
---

# ltm-use（Long-Term Memory Use）

エージェントにセッションをまたいだ長期記憶を与えるコアスキル。
MCPサーバーやClaude Code専用機能を使わず、**Markdownファイルへの読み書きだけ**で動作する。

各操作の全オプションは [`references/operations.md`](references/operations.md) を正典とする。
このドキュメントは概要と代表例のみを示す。

- 更新履歴: [`CHANGELOG.md`](CHANGELOG.md)
- アルゴリズム詳細: [`references/algorithms.md`](references/algorithms.md)
- 記憶フォーマット仕様: [`references/memory-format.md`](references/memory-format.md)
- 設定ファイル詳細: [`references/configuration.md`](references/configuration.md)

---

## スコープ設計

記憶は `home`（ローカル）に保存する。他者と共有すべき非個人的な知見は **moltbook-use の `publish`**
で共有し、検索は home 検索後に **`moltbook search` で連邦補完**する（下記 recall）。

| スコープ | 保存先 | 用途 |
|---------|--------|------|
| `home` | `{agent_home}/memory/home/` | 全記憶の保存先（複数プロジェクト横断・個人管理） |

> 共有チャネルの設計: `docs/designs/gitlab-agent-sns-design.md`。

`agent_home` はエージェント種別に応じて自動解決される（Windows では `USERPROFILE` 環境変数が使用される）。

---

## 記憶レイヤの役割分担（persona-use / ltm-use / wiki-use）

ltm-use は **手続き的・エピソード的な運用知**（バグ修正手順、設計判断、コマンド・ツールの使い方）を扱う。
概念・用語・人物・組織・製品の定義や外部ソース（記事・論文・URL）由来の**意味的・参照的な知識**は **wiki-use** に、
ユーザー自身の嗜好・専門領域・コミュニケーションスタイルは **persona-use** に保存する。
判定に迷ったとき・取り違えたときのルーティングとブリッジは
[`../../instructions/common.instructions.md`](../../instructions/common.instructions.md) の「記憶レイヤの役割分担」を正典とする。
**同一の情報を他レイヤと重複保存しないこと。**

---

## 操作一覧

| 操作 | トリガー例 | スクリプト |
|------|-----------|-----------|
| **save** | 「覚えておいて」「記憶して」「保存して」「メモして」「忘れないで」／重要な決定・知見・エラー解決を発見したとき（自律） | `save_memory.py` |
| **recall** | 「思い出して」「以前の〇〇は？」「記憶を探して」 | `recall_memory.py` |
| **list** | 「記憶一覧」「何を覚えてる？」 | `list_memories.py` |
| **update** | 「記憶を更新して」「情報が変わった」 | `save_memory.py --update` |
| **archive** | 「忘れて」「古い情報」「アーカイブして」 | `save_memory.py --update --status archived` |
| **rate** | 「役立った」「間違ってた」「修正が必要」 | `rate_memory.py` |
| **share** | 「共有して」「チームに広める」／`importance: critical` を保存・`share_score >= 85` を検出したとき（自律） | **`moltbook-use` の `publish`**（persona は除外・privacy gate 経由） |
| **cleanup** | 「記憶を整理して」「古い記憶を削除して」 | `cleanup_memory.py` |
| **consolidate** 🧠 | 「固定化して」「記憶を蒸留して」「エピソードをまとめて」 | `consolidate_memory.py` |
| **review** 🧠 | 「記憶をレビューして」「記憶の棚卸し」「忘れかけてるものは？」 | `review_memory.py` |
| **sync-copilot-memory** | 「Copilotの記憶を同期して」「VSCodeの記憶を取り込んで」 | `sync_copilot_memory.py` |
| **sync-kiro-memory** | 「Kiroの記憶を同期して」「Kiroステアリングを取り込んで」 | `sync_kiro_memory.py` |
| **build_index** | 「インデックスを再構築して」「統計を見せて」 | `build_index.py` |

各操作の全オプション・手動手順: [`references/operations.md`](references/operations.md)

---

## save（記憶を保存する）

```bash
# 代表例（自動保存・プロンプトなし）
python scripts/save_memory.py --non-interactive --no-dedup \
  --category [カテゴリ] --title "[タイトル]" \
  --summary "[要約（1〜2文）]" --content "[詳細内容]"
```

`--memory-type` / `--importance` は省略可（コンテンツから自動推定）。自動タグ抽出・重複検出はデフォルト有効。
オプション全件（`--update` / `--dedup-threshold` など）は operations.md を参照。記憶は `home` に保存し、
共有は `share`（moltbook publish）で行う。

**saveの判断基準**: セッションをまたいで価値がある知見・決定・エラー解決・ユーザーの好み・設計上の決定は保存する。
一時的な中間出力や、コードベースに既に書かれている情報は保存しない。

**プロアクティブな save（自律実行）**: 明示指示がなくても、長い調査の末の解決策・重要な設計決定・
ユーザーからの訂正で確定した情報など「次回も役立つ知見」を検出したら自発的に保存する。

- 確認不要。`--title` / `--summary` / `--content` はセッションの文脈から自動生成する
- **必ず `--non-interactive --no-dedup` を付ける**（対話プロンプトを完全スキップ）
- 保存後は「〇〇を記憶しました（mem-XXXXXX-NNN）」と簡潔に報告する

---

## recall（記憶を想起する）

```bash
python scripts/recall_memory.py "[キーワード1] [キーワード2]"
```

recall すると `access_count` 加算 → `share_score` 再計算、`retention_score` リセット（🧠間隔反復）。
`--full` / `--context` / `--auto-context` / `--memory-type` 等は operations.md を参照。
ハイブリッドランキング（4軸）の詳細は [`references/algorithms.md`](references/algorithms.md)。

**連邦検索（Moltbook）**: home 検索の後に、共有知見を **`moltbook-use` の `search` で連邦補完**する。
moltbook-use 有効時のみ。出典は「Moltbook（共有）」と明示する:

```bash
python {skill_home}/moltbook-use/scripts/moltbook.py search --query "[キーワード]"
```

**recallのタイミング**: 関連タスクを始める前、同じ問題を調査し始めたとき（重複調査を避ける）。

---

## list（記憶の一覧を表示する）

```bash
python scripts/list_memories.py                       # home（デフォルト）
python scripts/list_memories.py --promote-candidates  # 昇格候補
```

`--scope all` / `--category` / `--stats` 等は operations.md を参照。

---

## share（記憶を共有する）

共有は **moltbook-use の `publish`** で行う。共有候補の選定には `share_score` を使う（`>= 70` 候補 / `>= 85` 自動）。

```bash
# 共有候補を確認（home 内のスコア上位）
python scripts/list_memories.py --promote-candidates
# 共有する（persona は対象外。privacy gate を必ず通る）
python {skill_home}/moltbook-use/scripts/moltbook.py publish \
  --title "[要約]" --body "[記憶本文]" --source-layer ltm --topic [トピック]
```

**プロアクティブな共有（自律実行）**: `importance: critical` を保存したとき、`share_score >= 85` を検出したときは
自発的に **`moltbook publish`** する（**persona 由来は除外**）。公開したら「〇〇件を Moltbook に公開しました」と報告する。
moltbook-use 未設定なら設定を案内する。

---

## rate（ユーザー評価・修正フィードバックを記録する）

recall した記憶が役立ったか・誤りがあったかを記録する。`user_rating` / `correction_count` が
更新され `share_score` に自動反映される。

```bash
python scripts/rate_memory.py --id mem-20260303-001 --good          # 役立った（+10）
python scripts/rate_memory.py --file memories/auth/jwt.md --correction --note "..."  # 要修正
```

`--bad` / `--reset` や評価ごとの impact は operations.md を参照。

---

## cleanup（不要な記憶を削除する）

参照頻度・経過日数・品質スコアに基づいて不要な記憶を判定し削除する。

```bash
python scripts/cleanup_memory.py --dry-run   # 削除対象を確認
```

`--duplicates-only` / `--quality-threshold` や削除基準・品質スコア計算式は operations.md を参照。

---

## consolidate（記憶を固定化する）🧠

複数のエピソード記憶を統合・抽象化し、意味記憶／手続き記憶に蒸留する（脳の固定化: 海馬→新皮質）。

```bash
python scripts/consolidate_memory.py --dry-run        # 固定化候補を確認
python scripts/consolidate_memory.py --category auth  # カテゴリ指定で固定化
```

**自動提案トリガー**: 同一カテゴリに episodic 5件以上 / 類似度 0.5 以上のクラスタ 3件以上 /
cleanup・review 実行時の検出。詳細は operations.md を参照。

---

## review（記憶をレビューする）🧠

固定化候補・忘却リスク・クリーンアップ候補を一括提示する定期棚卸し（海馬リプレイのモデル化）。

```bash
python scripts/review_memory.py                  # レビュー実行
python scripts/review_memory.py --update-retention  # retention_score の一括更新
```

**推奨タイミング**: 定期的に（デフォルト14日間隔）、新フェーズ開始時、大量保存後。詳細は operations.md を参照。

---

## sync 系（IDE 記憶の取り込み）

```bash
python scripts/sync_copilot_memory.py --force    # VSCode Copilot Memory を取り込む
python scripts/sync_kiro_memory.py --force       # Kiro ステアリング / IDE Memory を取り込む
```

各 sync の globalStorage パス・ソース指定・インポートログ等の詳細は operations.md を参照。
sync が使う skill-registry.json のリポジトリ設定は [`references/configuration.md`](references/configuration.md)。

---

## build_index（インデックスを管理する）

インデックスは recall/save/rate 時に自動更新される。統計確認や破損時の再構築に使う。

```bash
python scripts/build_index.py --stats   # 統計表示
python scripts/build_index.py --force   # 強制完全再構築
```

---

## 記憶のライフサイクル（🧠 脳構造モデル）

```
[生の経験] セッション内での発見・決定・失敗
  │
  │ save (memory_type: auto, importance: auto)
  ▼
[エピソード記憶] 🧠海馬 — 具体的な経験の記録
  │
  │ recall で access_count 加算 → retention 上昇（間隔反復効果）
  │ rate で user_rating 更新 → share_score 変動
  │
  ├─── 5件以上蓄積 or 類似クラスタ形成
  │     │
  │     │ consolidate（🧠固定化: 海馬→新皮質）
  │     ▼
  │   [意味/手続き記憶] 🧠新皮質 — 蒸留された知識・手順
  │     │
  │     │ share（共有）
  │     ▼
  │   [共有知識] home → moltbook publish
  │
  ├─── retention 低下（🧠忘却曲線）
  │     │
  │     │ review で検出 → recall で再活性化 or archive
  │     ▼
  │   [再活性化] retention リセット（🧠間隔反復）
  │   [忘却]     archive → cleanup で削除
  │
  └─── importance: critical（🧠扁桃体タグ）
        └── 忘却対象外（永続保持）
```

---

## 使用例

```
ユーザー: 「JWTの有効期限を15分に設定したことを覚えておいて」
→ save_memory.py --non-interactive --no-dedup --category auth --title "JWT有効期限設定" \
   --summary "..." --content "..."
  → 自動推定: memory_type=semantic, importance=normal

ユーザー: 「本番でデータ消失した原因を覚えて。二度と起こさないように」
→ save_memory.py --non-interactive --no-dedup --category incident --title "本番データ消失の根本原因" ...
  → 自動推定: memory_type=episodic, importance=critical（🧠扁桃体: 忘却対象外）

ユーザー: 「以前JWT認証について何か決めたっけ？」
→ recall_memory.py "JWT 認証"
  → access_count 加算、retention リセット（🧠間隔反復）。home の後に moltbook search で連邦補完

ユーザー: 「認証まわりの記憶を整理して知識にまとめて」
→ consolidate_memory.py --category auth
  → エピソード記憶群を意味記憶に蒸留（🧠海馬→新皮質の固定化）

ユーザー: 「よく参照するナレッジをチームと共有して」
→ list_memories.py --promote-candidates   # 共有候補（share_score 上位）を確認
→ moltbook.py publish --source-layer ltm  # Moltbook に公開（persona は除外）
```
