# ltm-use 操作リファレンス

## 目次

- [save](#save記憶を保存する)
- [recall](#recall記憶を想起する)
- [list](#list記憶の一覧を表示する)
- [promote](#promote記憶を昇格共有する)
- [rate](#rateユーザー評価修正フィードバックを記録する)
- [build_index](#build_indexインデックスを管理する)
- [cleanup](#cleanup不要な記憶を削除する)
- [consolidate](#consolidate記憶を固定化する)（v5.0.0）
- [review](#review記憶をレビューする)（v5.0.0）
- [sync-copilot-memory](#sync-copilot-memoryvscode-copilot-memory-を取り込む)
- [sync](#syncgit共有領域から自動更新する)

---

各操作の全オプションと詳細説明。

---

## save（記憶を保存する）

### 全オプション

```bash
# 基本形（デフォルト: home）
python scripts/save_memory.py \
  --category [カテゴリ] \
  --title "[タイトル]" \
  --summary "[要約]" \
  --content "[詳細内容]" \
  --tags [タグ1],[タグ2]

# 非インタラクティブモード（自動保存・スクリプト呼び出し時）
--non-interactive        # 全input()プロンプトをスキップ（--no-dedup と併用推奨）

# スコープ指定（home / workspace）
--scope workspace        # ワークスペース固有（非推奨・廃止予定）

# v4.0.0 自動タグ抽出（デフォルト有効）
--no-auto-tags          # 自動タグ抽出を無効化

# v4.0.0 重複検出（デフォルト有効、閾値 0.65）
--no-dedup              # 重複検出をスキップ
--dedup-threshold 0.75  # 閾値を調整（0.0-1.0）

# 記憶の更新
--update --id mem-YYYYMMDD-NNN    # 既存記憶を更新
--update --file memories/auth/jwt.md

# ステータス変更
--status archived       # アーカイブ化（--update と併用）
--status deprecated     # 非推奨化（--update と併用）

# v5.0.0 記憶タイプ指定（脳の記憶分類に対応）
--memory-type episodic     # エピソード記憶（海馬: 具体的な経験・イベント）
--memory-type semantic     # 意味記憶（新皮質: 蒸留された知識・事実。デフォルト）
--memory-type procedural   # 手続き記憶（大脳基底核: 手順・パターン）

# v5.0.0 重要度指定（扁桃体モデル）
--importance critical      # 致命的障害・セキュリティ（忘却対象外）
--importance high          # 重要な設計決定（保持期間2倍）
--importance normal        # 通常（デフォルト）
--importance low           # 一時的メモ（保持期間半分）
```

### 手順（スクリプトなし・手動）

1. カテゴリを決定する（例: `auth`, `bug-investigation`, `general`）
2. `{agent_home}/memory/home/[カテゴリ]/[kebab-case-title].md` を作成する
3. フォーマット仕様（`memory-format.md`）に従ってフロントマターと本文を書く
4. **必須**: `summary` フィールドに1〜2文の要約を書く（検索の鍵）
5. `scope: home`, `access_count: 0`, `share_score: 0` を設定する

---

## recall（記憶を想起する）

### 全オプション

```bash
# 基本形（workspace → home → shared の順で自動検索）
python scripts/recall_memory.py "[キーワード1] [キーワード2]"

# スコープ指定
--scope workspace       # ワークスペースのみ
--scope home           # ホームのみ
--scope shared         # 共有のみ
--scope all            # 全スコープ

# v4.0.0 ハイブリッドランキング（デフォルト有効）
--no-hybrid            # v3 互換モード（キーワード一致のみ）

# 表示オプション
--full                 # 全文表示（summary だけでなく body も）
--limit 10             # 表示件数（デフォルト5）

# トラッキング
--no-track             # access_count を更新しない

# 評価ループ
--rate-after           # 結果表示後にインタラクティブ評価を開始

# v5.0.0 文脈依存想起（前頭前皮質モデル）
--context "認証リファクタリング"  # 作業コンテキストを指定して関連性ブースト
--auto-context                 # git diff / ディレクトリからコンテキストを自動推定

# v5.0.0 記憶タイプフィルター
--memory-type episodic         # エピソード記憶のみ検索
--memory-type semantic         # 意味記憶のみ検索
--memory-type procedural       # 手続き記憶のみ検索
```

### 手順（スクリプトなし・手動）

1. `${MEMORY_DIR}/` 以下のサブディレクトリを列挙してカテゴリを把握する
2. 各 `.md` ファイルの `summary` フィールドをスキャンしてキーワードとの関連を判断する
3. 関連するファイルを全文読み込みして内容を把握する
4. 見つからない場合は `~/.copilot/memory/home/` や `~/.copilot/memory/shared/` を同様にスキャンする
5. `access_count` をインクリメントし `last_accessed` を今日の日付に更新する

---

## list（記憶の一覧を表示する）

### 全オプション

```bash
# 基本形（workspace のみ）
python scripts/list_memories.py

# スコープ指定
--scope home           # ホームのみ
--scope shared         # 共有のみ
--scope all            # 全スコープ

# フィルター
--promote-candidates   # 昇格候補のみ（share_score >= 70）
--category auth        # 特定カテゴリのみ
--status active        # 特定ステータスのみ

# 表示オプション
--stats                # 統計のみ（記憶一覧は表示しない）
--verbose              # 詳細表示
```

---

## promote（記憶を昇格・共有する）

### 全オプション

```bash
# 基本形（半自動：各記憶を確認しながら workspace → home）
python scripts/promote_memory.py

# 昇格候補確認（ドライラン）
--list                 # 実行せず候補のみ表示

# 自動昇格（score >= 85）
--auto                 # 確認なしで全て昇格

# スコープ・ターゲット指定
--scope workspace --target home     # workspace → home（デフォルト）
--scope home --target shared        # home → shared（git commit も実施）

# 個別指定
--id mem-20260303-001  # 特定記憶のみ昇格
--file memories/auth/jwt.md
```

### 昇格後の push

```bash
# shared への push（git-skill-manager のリポジトリを使用）
python scripts/sync_memory.py --push
python scripts/sync_memory.py --push --repo origin
```

---

## rate（ユーザー評価・修正フィードバックを記録する）

### 全オプション

```bash
# 役立った（share_score +10）
python scripts/rate_memory.py --id mem-20260303-001 --good
python scripts/rate_memory.py --file memories/auth/jwt.md --good

# 役に立たなかった（share_score -10）
--bad

# 誤り・修正が必要（share_score -15以上、correction_count +1）
--correction --note "JWTの有効期限を30分に変更した"

# 評価リセット
--reset                # user_rating を 0 にリセット
```

### 評価の impact

| 評価 | user_rating | share_score 変動 | その他 |
|------|-------------|------------------|--------|
| `--good` | +1 | +10点（最大+20まで累積） | - |
| `--bad` | -1 | -10点 | - |
| `--correction` | -1 | -15〜-20点 | correction_count +1、修正ログ追記 |

---

## build_index（インデックスを管理する）

### 全オプション

```bash
# 統計表示（インデックス状況・記憶品質サマリー）
python scripts/build_index.py --stats

# スコープ指定
--scope home           # ホームのみ
--scope shared         # 共有のみ
--scope all            # 全スコープ

# 再構築
--force                # 強制完全再構築（インデックス破損時）

# 通常実行（増分更新、自動で実行されるため通常不要）
python scripts/build_index.py
```

---

## cleanup（不要な記憶を削除する）

### 全オプション

```bash
# ドライラン（削除対象を確認のみ）
python scripts/cleanup_memory.py --dry-run

# 基本形（ワークスペースのみ）
python scripts/cleanup_memory.py

# スコープ指定
--scope home           # ホームのみ
--scope shared         # 共有のみ
--scope all            # 全スコープ

# 確認スキップ
--yes                  # 確認なしで削除（非対話環境用）

# v4.0.0 重複検出モード
--duplicates-only      # 類似度 >= 0.85 のペアの低品質側を削除候補に
--dedup-threshold 0.90 # 重複検出閾値を調整（デフォルト 0.85）

# v4.0.0 品質スコア閾値モード
--quality-threshold 30 # 総合品質スコア < 30 を削除候補に
```

### 削除基準（デフォルト）

- `access_count == 0` かつ作成から 30日以上経過
- `status == archived` かつ更新から 60日以上経過
- `status == deprecated`

設定ファイル（`~/.copilot/memory/config.json`）で閾値を変更可能:
- `cleanup_inactive_days`: デフォルト 30
- `cleanup_archived_days`: デフォルト 60

---

## consolidate（記憶を固定化する）

> **v5.0.0 新規操作**
> 脳科学的背景: 海馬のエピソード記憶が睡眠中のリプレイを経て新皮質の意味記憶に転写される
> 「記憶の固定化（consolidation）」プロセスをモデル化。

### 全オプション

```bash
# 固定化候補を確認（ドライラン）
python scripts/consolidate_memory.py --dry-run

# カテゴリ指定で固定化
python scripts/consolidate_memory.py --category auth

# 特定のエピソード記憶群を固定化
python scripts/consolidate_memory.py \
  --ids mem-20260301-001,mem-20260305-002,mem-20260308-003

# 生成される記憶タイプを指定
--output-type semantic      # 意味記憶として蒸留（デフォルト）
--output-type procedural    # 手続き記憶として蒸留

# スコープ指定
--scope workspace           # ワークスペース（デフォルト）
--scope home                # ホーム

# 確認なし
--yes                       # 確認プロンプトをスキップ
```

### 固定化フロー

```
1. 対象のエピソード記憶群を特定
   - 同カテゴリ内の episodic 記憶（5件以上、または --ids で指定）
   - TF-IDF 類似度でクラスタリング

2. 蒸留内容を生成
   - 共通する知見・ルール・パターンを抽出
   - 具体的な日時・文脈を除去（一般化）
   - 矛盾する情報は最新を優先

3. 新しい semantic/procedural 記憶を生成
   - consolidated_from: [元のエピソードID一覧]
   - importance: 元の最高レベルを継承
   - share_score: 元の平均 × 1.2

4. 元のエピソード記憶を archived + consolidated_to を設定
```

### 固定化の出力例

```
🧠 固定化候補:

[クラスタ 1] auth カテゴリ（5件のエピソード記憶）
  - mem-20260301-001 "JWT期限エラーの修正"
  - mem-20260305-002 "OAuthトークン更新の問題"
  - mem-20260308-003 "セッション切れバグ修正"
  - mem-20260310-001 "認証ミドルウェアのリファクタ"
  - mem-20260311-002 "CORS認証ヘッダーの設定"

→ 蒸留された意味記憶:
  title: "認証システムの設計知見"
  memory_type: semantic
  summary: "JWT/OAuth/セッション管理における共通パターンと注意点"
  importance: high

固定化を実行しますか？ (y/n)
```

---

## review（記憶をレビューする）

> **v5.0.0 新規操作**
> 脳科学的背景: 睡眠中に海馬が記憶をリプレイし、重要な記憶を強化・不要な記憶を忘却する
> プロセスをモデル化。定期的な記憶の棚卸しを行う。

### 全オプション

```bash
# 記憶のレビュー（全カテゴリ）
python scripts/review_memory.py

# スコープ指定
--scope workspace           # ワークスペース（デフォルト）
--scope home                # ホーム
--scope all                 # 全スコープ

# 特定カテゴリのみ
--category auth

# レビュー項目を限定
--consolidation-only        # 固定化候補のみ
--forgetting-only           # 忘却リスクのみ
--cleanup-only              # クリーンアップ候補のみ

# retention_score の一括更新（レビュー結果なし）
--update-retention          # 全記憶の retention_score を再計算
```

### レビュー出力例

```
=== 🧠 記憶レビュー（workspace: 42件） ===

📌 固定化候補（エピソード→意味記憶への蒸留推奨）:
  [1] auth カテゴリに 5件のエピソード記憶
      → `consolidate --category auth` で蒸留を推奨

⚠ 忘却リスク（retention < 0.3 かつ価値のある記憶）:
  [2] mem-20260201-003 "API設計ガイドライン"
      retention: 0.25 | share_score: 65 | importance: normal
      → recall して再活性化、または importance を high に変更を推奨

  [3] mem-20260210-001 "デプロイ手順（staging）"
      retention: 0.18 | share_score: 45 | importance: normal
      → recall して再活性化を推奨

🗑 クリーンアップ候補（retention < 0.1 かつ低スコア）:
  [4] mem-20260115-001 "一時的デバッグメモ"
      retention: 0.05 | share_score: 12 | importance: low
      → archive を推奨

📊 統計サマリー:
  episodic: 18件 | semantic: 20件 | procedural: 4件
  critical: 2件 | high: 8件 | normal: 28件 | low: 4件
  平均 retention: 0.62 | 平均 share_score: 43
```

---

## sync-copilot-memory（VSCode Copilot Memory を取り込む）

### 全オプション

```bash
# ドライラン（ファイルを作成せず確認のみ）
python scripts/sync_copilot_memory.py --dry-run

# 基本形（home スコープにインポート）
python scripts/sync_copilot_memory.py

# スコープ指定
--scope workspace      # ワークスペースにインポート
--scope home           # ホームにインポート（デフォルト）

# globalStorage パス指定
--storage "/path/to/globalStorage"

# デバッグ・調査
--list-keys            # state.vscdb の全キーを表示

# 強制再インポート
--force                # インポート済みIDを無視
```

### globalStorage の場所

| OS | VSCode | VSCode Insiders | Cursor |
|---|---|---|---|
| Windows | `%APPDATA%\Code\User\globalStorage\` | `%APPDATA%\Code - Insiders\User\globalStorage\` | `%APPDATA%\Cursor\User\globalStorage\` |
| macOS | `~/Library/Application Support/Code/User/globalStorage/` | `~/Library/Application Support/Code - Insiders/User/globalStorage/` | `~/Library/Application Support/Cursor/User/globalStorage/` |
| Linux | `~/.config/Code/User/globalStorage/` | `~/.config/Code - Insiders/User/globalStorage/` | `~/.config/Cursor/User/globalStorage/` |

### インポート後の推奨操作

```bash
# インポート結果を確認
python scripts/recall_memory.py "copilot-memory" --scope home

# 一覧表示
python scripts/list_memories.py --scope home

# 役立ったものを評価（share_score を上げて昇格候補にする）
python scripts/rate_memory.py --file memories/copilot-memory/xxx.md --good
```

---

## sync（git共有領域から自動更新する）

### 全オプション

```bash
# 基本形（全リポジトリを pull して差分確認）
python scripts/sync_memory.py

# リポジトリ指定
--repo origin          # 特定リポジトリのみ

# インポート
--import-to-home       # 新しい shared 記憶を home に取り込む

# 検索
--search "API設計"     # 全 shared からキーワード検索

# push
--push                 # readonly でないリポジトリへ push
--push --repo origin   # 特定リポジトリへ push

# フォールバック設定
--set-remote git@github.com:org/memories.git  # skill-registry.json 未設定時の remote
```

skill-registry.json の詳細は `configuration.md` を参照。
