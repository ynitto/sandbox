# ltm-use 操作リファレンス

## 目次

- [save](#save記憶を保存する)
- [recall](#recall記憶を想起する)
- [list](#list記憶の一覧を表示する)
- [promote](#promote記憶を昇格共有する)
- [rate](#rateユーザー評価修正フィードバックを記録する)
- [build_index](#build_indexインデックスを管理する)
- [cleanup](#cleanup不要な記憶を削除する)
- [sync-copilot-memory](#sync-copilot-memoryvscode-copilot-memory-を取り込む)
- [sync](#syncgit共有領域から自動更新する)

---

各操作の全オプションと詳細説明。

---

## save（記憶を保存する）

### 全オプション

```bash
# 基本形
python ${SKILL_DIR}/scripts/save_memory.py \
  --category [カテゴリ] \
  --title "[タイトル]" \
  --summary "[要約]" \
  --content "[詳細内容]" \
  --tags [タグ1],[タグ2]

# スコープ指定（workspace / home）
--scope home

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
```

### 手順（スクリプトなし・手動）

1. カテゴリを決定する（例: `auth`, `bug-investigation`, `general`）
2. `${MEMORY_DIR}/[カテゴリ]/[kebab-case-title].md` を作成する
3. フォーマット仕様（`memory-format.md`）に従ってフロントマターと本文を書く
4. **必須**: `summary` フィールドに1〜2文の要約を書く（検索の鍵）
5. `scope`, `access_count: 0`, `share_score: 0` を設定する

---

## recall（記憶を想起する）

### 全オプション

```bash
# 基本形（workspace → home → shared の順で自動検索）
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード1] [キーワード2]"

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
python ${SKILL_DIR}/scripts/list_memories.py

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
python ${SKILL_DIR}/scripts/promote_memory.py

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
python ${SKILL_DIR}/scripts/sync_memory.py --push
python ${SKILL_DIR}/scripts/sync_memory.py --push --repo origin
```

---

## rate（ユーザー評価・修正フィードバックを記録する）

### 全オプション

```bash
# 役立った（share_score +10）
python ${SKILL_DIR}/scripts/rate_memory.py --id mem-20260303-001 --good
python ${SKILL_DIR}/scripts/rate_memory.py --file memories/auth/jwt.md --good

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
python ${SKILL_DIR}/scripts/build_index.py --stats

# スコープ指定
--scope home           # ホームのみ
--scope shared         # 共有のみ
--scope all            # 全スコープ

# 再構築
--force                # 強制完全再構築（インデックス破損時）

# 通常実行（増分更新、自動で実行されるため通常不要）
python ${SKILL_DIR}/scripts/build_index.py
```

---

## cleanup（不要な記憶を削除する）

### 全オプション

```bash
# ドライラン（削除対象を確認のみ）
python ${SKILL_DIR}/scripts/cleanup_memory.py --dry-run

# 基本形（ワークスペースのみ）
python ${SKILL_DIR}/scripts/cleanup_memory.py

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

## sync-copilot-memory（VSCode Copilot Memory を取り込む）

### 全オプション

```bash
# ドライラン（ファイルを作成せず確認のみ）
python ${SKILL_DIR}/scripts/sync_copilot_memory.py --dry-run

# 基本形（home スコープにインポート）
python ${SKILL_DIR}/scripts/sync_copilot_memory.py

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
python ${SKILL_DIR}/scripts/recall_memory.py "copilot-memory" --scope home

# 一覧表示
python ${SKILL_DIR}/scripts/list_memories.py --scope home

# 役立ったものを評価（share_score を上げて昇格候補にする）
python ${SKILL_DIR}/scripts/rate_memory.py --file memories/copilot-memory/xxx.md --good
```

---

## sync（git共有領域から自動更新する）

### 全オプション

```bash
# 基本形（全リポジトリを pull して差分確認）
python ${SKILL_DIR}/scripts/sync_memory.py

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
