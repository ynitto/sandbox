---
name: ltm-use
description: セッションをまたいで知識・決定事項を継続させたいときのスキル。「覚えておいて」でsave、「思い出して」でrecall、「記憶一覧」でlist、「忘れて」でarchive、「昇格して」でpromote、「整理して」でcleanup、「役立った／間違ってた」でrate、「固定化して」でconsolidate。重要な知見を発見したら自律的にsaveを実行すること。
metadata:
  version: 5.2.0
  tier: core
  category: meta
  tags:
    - memory
    - knowledge-persistence
    - recall
    - brain-inspired
---

# ltm-use（Long-Term Memory Use）

エージェントにセッションをまたいだ長期記憶を与えるコアスキル。
MCPサーバーやClaude Code専用機能を使わず、**Markdownファイルへの読み書きだけ**で動作する。

更新履歴: [`CHANGELOG.md`](CHANGELOG.md)
アルゴリズム詳細: [`references/algorithms.md`](references/algorithms.md)
設計思想: [`../../docs/designs/ltm-use-v5-brain-design.md`](../../docs/designs/ltm-use-v5-brain-design.md)

---

## スコープ設計

```
home  →  (昇格・git)  →  shared
  ↑                            ↑
ユーザー横断               チーム共有
ローカル永続              git管理
```

| スコープ | 保存先 | 用途 | git管理 |
|---------|--------|------|---------|
| `home` | `{agent_home}/memory/home/` | 全記憶のデフォルト保存先（複数プロジェクト横断） | 個人管理（ローカル） |
| `shared` | `{agent_home}/memory/shared/<repo名>/memories/` | チーム共有すべき知見 | **git管理（skill-registry.json のリポジトリを使用）** |

> **注意**: `workspace` スコープ（`${SKILL_DIR}/memories/`）は廃止。全記憶は `home` に保存する。

`agent_home` はエージェント種別に応じて自動解決される（Windows では `USERPROFILE` 環境変数が使用される）。

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
| **promote** | 「昇格して」「共有知識にして」「チームに広める」／`importance: critical` な記憶を保存したとき・home の `share_score >= 85` を検出したとき（自律） | `promote_memory.py` |
| **cleanup** | 「記憶を整理して」「古い記憶を削除して」 | `cleanup_memory.py` |
| **consolidate** 🧠 | 「固定化して」「記憶を蒸留して」「エピソードをまとめて」 | `consolidate_memory.py` |
| **review** 🧠 | 「記憶をレビューして」「記憶の棚卸し」「忘れかけてるものは？」 | `review_memory.py` |
| **sync** | 「チームの記憶を取り込んで」「共有知識を更新して」 | `sync_memory.py` |
| **sync-copilot-memory** | 「Copilotの記憶を同期して」「VSCodeの記憶を取り込んで」「Copilot Memoryをインポートして」 | `sync_copilot_memory.py` |
| **sync-kiro-memory** | 「Kiroの記憶を同期して」「Kiroステアリングを取り込んで」「Kiro IDEの記憶をインポートして」 | `sync_kiro_memory.py` |
| **build_index** | 「インデックスを再構築して」「統計を見せて」 | `build_index.py` |

詳細オプション: [`references/operations.md`](references/operations.md)

---

## save（記憶を保存する）

```bash
# ホーム記憶として保存（デフォルト・全記憶はここに入る）
python scripts/save_memory.py \
  --category [カテゴリ] \
  --title "[タイトル]" \
  --summary "[要約（1〜2文）]" \
  --content "[詳細内容]" \
  --tags [タグ1],[タグ2]

# 自動保存（プロンプト一切なし）
python scripts/save_memory.py --non-interactive --no-dedup \
  --category [カテゴリ] --title "[タイトル]" --summary "[要約]" --content "[内容]"

# 記憶タイプ指定（🧠 脳の記憶分類に対応）
python scripts/save_memory.py \
  --memory-type episodic \       # 海馬: 具体的な経験・イベント
  --importance high \             # 扁桃体: 重要度レベル
  --category auth --title "JWT期限エラーの調査" \
  --summary "..." --content "..."
  # → memory_type/importance は省略可（コンテンツから自動推定）

# 自動タグ抽出と重複検出
python scripts/save_memory.py \
  --category auth --title "JWT認証の実装" \
  --summary "..." --content "..."
  # → 自動的にタグを提案し、類似記憶を検出して統合・更新・別保存を選択できる


# 重複検出をスキップ（強制保存）
python scripts/save_memory.py --no-dedup \
  --category auth --title "[タイトル]" --summary "[要約]" --content "[内容]"

# 重複検出閾値を調整（デフォルト 0.65）
python scripts/save_memory.py --dedup-threshold 0.75 \
  --category auth --title "[タイトル]" --summary "[要約]" --content "[内容]"

# 自動タグ抽出をスキップ（手動タグのみ）
python scripts/save_memory.py --no-auto-tags \
  --category auth --title "[タイトル]" --summary "[要約]" --content "[内容]" --tags jwt,auth
```

> **注意**: `--scope shared` は `save_memory.py` では非対応。shared への保存は
> `promote_memory.py` で home から昇格する手順を使うこと。


**saveの判断基準**:
- ✅ セッションをまたいで価値がある情報
- ✅ 調査・決定・失敗から得た知見
- ✅ バグ・エラーの原因と解決策
- ✅ ユーザーの好み・プロジェクト固有のルール・制約
- ✅ 重要な設計上の決定とその理由
- ✅ 再調査を避けられる「詰まりポイント」の解決策
- ❌ 一時的な中間出力・作業ログ
- ❌ コードベースにすでに書かれている情報


**プロアクティブなsave（自律的保存）**:

明示的な「覚えておいて」がなくても、以下の状況では**自発的に** `save_memory.py` を実行すること：

- 長い調査・デバッグの末に解決策が見つかったとき
- ユーザーが重要な設計・アーキテクチャ上の決定を下したとき
- 同じ問題で詰まることを避けられる知見が得られたとき
- ユーザーから訂正・フィードバックを受けて正しい情報が確定したとき
- セッション内で新しい重要な事実が判明し、次回のセッションでも役立つと判断したとき

**自律的保存時のルール**:
- 保存前にユーザーへの確認は**不要**
- `--title`・`--summary`・`--content` は**必ずセッションの文脈から自動生成**して指定すること（ユーザーに入力を求めない）
- **必ず `--non-interactive --no-dedup` を付ける**こと（対話プロンプトを完全スキップ）
- saveした後、「〇〇を記憶しました（mem-XXXXXX-NNN）」と簡潔に報告する

```bash
# 自律的保存の例（ユーザーへの確認・入力一切なし）
python scripts/save_memory.py --non-interactive --no-dedup \
  --category auth --title "JWT有効期限設定" \
  --summary "JWTアクセストークンを15分に設定。セキュリティとUXのバランスから決定。" \
  --content "セッション内で決定した内容の詳細..."
```

---

## recall（記憶を想起する）

recall すると `access_count` が自動加算され `share_score` が再計算される。
home で見つからない場合は shared を自動フォールバック検索する。
recall 時に `retention_score` も自動更新（間隔反復効果による忘却曲線リセット）。

```bash
python scripts/recall_memory.py "[キーワード1] [キーワード2]"

# 全文表示
python scripts/recall_memory.py "[キーワード]" --full

# 結果に対してインタラクティブ評価
python scripts/recall_memory.py "[キーワード]" --rate-after

# 文脈依存想起（🧠 前頭前皮質モデル）
python scripts/recall_memory.py "[キーワード]" \
  --context "認証システムのリファクタリング"
# → キーワード一致だけでなく、作業コンテキストとの関連性もランキングに反映

# 自動コンテキスト（git diff / ディレクトリから推定）
python scripts/recall_memory.py --auto-context

# 記憶タイプフィルター
python scripts/recall_memory.py "[キーワード]" --memory-type procedural
```

**recallのタイミング**:
- 関連するタスクを始める前
- 同じ問題を調査し始めたとき（重複調査を避ける）

ハイブリッドランキング（4軸）詳細: [`references/algorithms.md`](references/algorithms.md)

---

## list（記憶の一覧を表示する）

```bash
python scripts/list_memories.py                    # home（デフォルト）
python scripts/list_memories.py --scope all        # 全スコープ
python scripts/list_memories.py --promote-candidates  # 昇格候補
python scripts/list_memories.py --stats            # 統計のみ
```

---

## promote（記憶を昇格・共有する）

`share_score >= 70` で昇格候補、`>= 85` で自動昇格対象。
`recall` を繰り返すほど `share_score` が上がり、昇格対象になる。

> **閾値の変更**: `<AGENT_HOME>/memory/config.json` の `semi_auto_promote_threshold`（デフォルト70）と
> `auto_promote_threshold`（デフォルト85）で調整可能。

```bash
# 昇格候補を確認（ドライラン）
python scripts/promote_memory.py --list

# 半自動昇格（各記憶を確認しながら home → shared）
python scripts/promote_memory.py

# 自動昇格（score >= 85 を全て昇格）
python scripts/promote_memory.py --auto

# home → shared（git commit も実施）
python scripts/promote_memory.py --scope home --target shared --auto

# home → shared（git commit + push まで一括）
python scripts/promote_memory.py --scope home --target shared --auto --push

# git push のみ（別途実行する場合）
python scripts/sync_memory.py --push
```

**昇格フロー**:
```
home → shared:     個人ナレッジ → チーム共有（git commit → push で共有）
```

**プロアクティブなpromote/push（自律的共有）**:

明示的な「昇格して」がなくても、以下の状況では**自発的に** promote → push を実行すること：

- `importance: critical` な記憶を保存したとき → 即座に `home → shared` 昇格 + push
- home スコープの記憶の `share_score >= 85` を検出したとき → 自動昇格 + push
- セッション内で重要な知見を複数 save した後 → `--list` で昇格候補を確認し、候補があればユーザーに報告

共有リポジトリが未設定の場合は push をスキップし、設定を案内すること。
promote/push 後は「〇〇件を shared に昇格・push しました」と簡潔に報告する。

```bash
# 自律共有フロー（one-shot: commit + push まで一括）
python scripts/promote_memory.py --scope home --target shared --auto --push
```

---

## rate（ユーザー評価・修正フィードバックを記録する）

recall した記憶が役立ったか、誤りがあったかを記録する。
`user_rating` と `correction_count` が更新され `share_score` に自動反映される。

```bash
# 役立った記憶を評価（share_score +10）
python scripts/rate_memory.py --id mem-20260303-001 --good

# 誤りがあった・修正が必要な記憶（share_score -15以上）
python scripts/rate_memory.py --file memories/auth/jwt.md \
  --correction --note "JWTの有効期限を30分に変更した"

# 役に立たなかった記憶（share_score -10）
python scripts/rate_memory.py --file memories/auth/jwt.md --bad
```

---

## build_index（インデックスを管理する）

インデックスは recall/save/rate 時に自動更新される。
統計確認や強制再構築に使用する。

```bash
# 統計を表示（インデックス状況・記憶品質サマリー）
python scripts/build_index.py --stats

# 全スコープの統計
python scripts/build_index.py --scope all --stats

# 強制完全再構築（インデックス破損時）
python scripts/build_index.py --force
```

---

## cleanup（不要な記憶を削除する）

参照頻度・経過日数に基づいて不要な記憶を自動判定し削除する。

重複検出モードと品質スコア閾値による智的クリーンアップをサポート。

```bash
# ドライラン（削除対象を確認）
python scripts/cleanup_memory.py --dry-run

# ホーム記憶をクリーンアップ（デフォルト）
python scripts/cleanup_memory.py

# 重複検出モード（類似度 >= 0.85 のペアを検出）
python scripts/cleanup_memory.py --duplicates-only --dry-run

# 品質スコア閾値モード（総合品質 < 30 を削除候補に）
python scripts/cleanup_memory.py --quality-threshold 30 --dry-run
```

削除基準・品質スコア計算式の詳細: [`references/operations.md`](references/operations.md)

---

## sync-copilot-memory（VSCode Copilot Memory を取り込む）

VSCode の Copilot Memory 機能（`%APPDATA%\Code\User\globalStorage\github.copilot-chat\`）に
保存されたメモリを自動検出し、ltm-use の記憶ファイルへ変換してインポートする。

新規エントリのみを取り込み、重複インポートを避けるため
`{MEMORY_DIR}/copilot-memory/.copilot-import-log.json` にインポート済みIDを記録する。

```bash
# 何が見つかるか確認するだけ（ファイルを作成しない）
python scripts/sync_copilot_memory.py --dry-run

# home スコープに取り込む（デフォルト・プロジェクト横断）
# ユーザーからの明示的な指示なので --force でインターバルを無視して実行する
python scripts/sync_copilot_memory.py --force

# shared スコープに取り込む
python scripts/sync_copilot_memory.py --scope shared --force
```

globalStorageパス・詳細オプション: [`references/operations.md`](references/operations.md)

---

## sync-kiro-memory（Kiro ステアリング / IDE Memory を取り込む）

Kiro CLI のグローバルステアリングファイル（`~/.kiro/steering/*.md`）および
Kiro IDE の globalStorage に保存されたメモリを自動検出し、ltm-use へ変換してインポートする。

ステアリングファイルは全プロジェクト共通の永続ルール・規約として特に有用。
重複インポートを避けるため `{MEMORY_DIR}/kiro-memory/.kiro-import-log.json` に記録する。

```bash
# 何が見つかるか確認するだけ（ファイルを作成しない）
python scripts/sync_kiro_memory.py --dry-run

# home スコープに取り込む（デフォルト・プロジェクト横断）
# ユーザーからの明示的な指示なので --force でインターバルを無視して実行する
python scripts/sync_kiro_memory.py --force

# ステアリングファイルのみ取り込む
python scripts/sync_kiro_memory.py --source steering --force

# Kiro IDE globalStorage のみ取り込む
python scripts/sync_kiro_memory.py --source ide --force
```

グローバルステアリングパス・詳細オプション: [`references/operations.md`](references/operations.md)

---

## sync（git共有領域から自動更新する）

skill-registry.json に登録されたリポジトリ（git-skill-manager と共通）を使用する。
複数リポジトリ・readonly 対応。

```bash
# 全リポジトリを pull して差分確認
python scripts/sync_memory.py

# 新しい shared 記憶を home に取り込む
python scripts/sync_memory.py --import-to-home

# push（readonly でないリポジトリへ）
python scripts/sync_memory.py --push
```

---

## 設定

`<AGENT_HOME>/skill-registry.json`（git-skill-manager と共通）と `<AGENT_HOME>/memory/config.json` で動作をカスタマイズできる。

設定詳細: [`references/configuration.md`](references/configuration.md)

---

## consolidate（記憶を固定化する）🧠

複数のエピソード記憶を統合・抽象化し、意味記憶（知識）または手続き記憶（手順）に蒸留する。
脳の「記憶の固定化」（海馬→新皮質への転写）をモデル化。

```bash
# 固定化候補を確認
python scripts/consolidate_memory.py --dry-run

# カテゴリ指定で固定化
python scripts/consolidate_memory.py --category auth

# 特定のエピソード記憶群を固定化
python scripts/consolidate_memory.py \
  --ids mem-20260301-001,mem-20260305-002,mem-20260308-003
```

**固定化の自動提案トリガー**:
- 同一カテゴリ内にエピソード記憶が 5件以上蓄積
- 類似度 0.5 以上のエピソード記憶が 3件以上のクラスタを形成
- cleanup/review 実行時に固定化候補を検出

詳細オプション: [`references/operations.md`](references/operations.md)

---

## review（記憶をレビューする）🧠

海馬のリプレイ（睡眠中の記憶再活性化）をモデル化。
固定化候補・忘却リスク・クリーンアップ候補を一括提示する定期棚卸し機能。

```bash
# 記憶のレビュー
python scripts/review_memory.py

# 全スコープ
python scripts/review_memory.py --scope all

# retention_score の一括更新
python scripts/review_memory.py --update-retention
```

**レビューの推奨タイミング**:
- 定期的に（デフォルト14日間隔）
- 新しいプロジェクトフェーズの開始時
- 大量の記憶を保存した後

詳細オプション: [`references/operations.md`](references/operations.md)

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
  │     │ promote（昇格）
  │     ▼
  │   [共有知識] home → shared
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

## リファレンス

- **記憶フォーマット仕様**: [`references/memory-format.md`](references/memory-format.md)
- **操作の詳細オプション**: [`references/operations.md`](references/operations.md)
- **アルゴリズム詳細**: [`references/algorithms.md`](references/algorithms.md)
- **設定ファイル詳細**: [`references/configuration.md`](references/configuration.md)

---

## 使用例

```
ユーザー: 「JWTの有効期限を15分に設定したことを覚えておいて」
→ save_memory.py --non-interactive --no-dedup --category auth --title "JWT有効期限設定" \
   --summary "JWTアクセストークンを15分に設定。セキュリティとUXのバランスから決定。" \
   --content "..."
  → 自動推定: memory_type=semantic, importance=normal

ユーザー: 「さっきJWTの期限切れでエラーになった件を記録して」
→ save_memory.py --non-interactive --no-dedup --category auth --title "JWT期限切れエラーの調査" \
   --summary "リフレッシュトークン未処理でセッション切れが発生。" \
   --content "2026-03-12に発生..."
  → 自動推定: memory_type=episodic, importance=normal

ユーザー: 「本番でデータ消失した原因を覚えて。二度と起こさないように」
→ save_memory.py --non-interactive --no-dedup --category incident --title "本番データ消失の根本原因" \
   --summary "..." --content "..."
  → 自動推定: memory_type=episodic, importance=critical（🧠扁桃体: 忘却対象外）

ユーザー: 「以前JWT認証について何か決めたっけ？」
→ recall_memory.py "JWT 認証"
  → access_count が加算、retention_score がリセット（🧠間隔反復）
  → 見つからなければ home/shared を自動検索

ユーザー: 「認証まわりの記憶を整理して知識にまとめて」
→ consolidate_memory.py --category auth
  → エピソード記憶群を意味記憶に蒸留（🧠海馬→新皮質の固定化）

ユーザー: 「忘れかけてる記憶はある？」
→ review_memory.py
  → retention < 0.3 の記憶を検出、再活性化 or archive を提案（🧠海馬リプレイ）

ユーザー: 「よく参照するナレッジをチームと共有して」
→ list_memories.py --promote-candidates  # 昇格候補を確認
→ promote_memory.py --auto --push        # 自動昇格（home → shared） + push まで一括
```
