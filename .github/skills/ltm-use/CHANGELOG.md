# CHANGELOG

## v5.3.0（2026-03-31）

### Breaking Changes

- **`workspace` スコープを廃止**。全記憶は `home` スコープに保存する。
  - `save_memory.py`, `recall_memory.py`, `list_memories.py`, `cleanup_memory.py`,
    `consolidate_memory.py`, `build_index.py`, `promote_memory.py` の `--scope` 引数から
    `workspace` オプションを削除。
  - `memory_utils.py` の `SCOPE_DIRS["workspace"]` を削除。
  - 既存の `workspace` 記憶（`${SKILL_DIR}/memories/`）を引き続き使用するには
    `<AGENT_HOME>/memory/home/` へ手動でコピーすること。
- **`promote_memory.py` の昇格フロー変更**。`workspace → home → shared` の2段階から
  `home → shared` の1段階に統一。`--scope` は `home` のみをサポート。

### Bug Fixes

- **`recall_memory.py`**: body キーワードスコア（0〜20）を base_score（0.0〜1.0）に
  直接加算していたスケール不整合を修正。`min(body_score/50, 0.2)` で正規化し
  最大 +0.2 の補正として加算するよう変更。

### Improvements

- **`cleanup_memory.py`**: `retention_score < 0.1` かつ `importance` が
  `critical`/`high` でない記憶を削除候補に追加（`memory-format.md` 仕様に準拠）。
- **`consolidate_memory.py`**: `memory_type` 未設定のレガシーファイルに対して
  `detect_memory_type()` による自動推定を適用し、episodic な記憶を固定化対象に含める。
- **`memory_utils.py`**: `DEFAULT_MEMORY_TYPE = "semantic"` 定数を追加し、
  各スクリプトのハードコードを統一。
- **`memory_utils.py`**: `compute_retention_score()` の config キー名を
  `retention_base_half_life` → `forgetting_base_half_life` に修正
  （`configuration.md` の定義と統一）。

---

## v5.2.0（2026-03-30）

### 修正

- **`--no-hybrid` 未実装を解消**: `recall_memory.py` の argparser に `--no-hybrid` を追加し、`search_all_scopes` / `fallback_search` を通じて `search_with_index` の `use_hybrid` フラグに伝播するよう修正
- **`update_memory` に `memory_type`/`importance` を追加**: `save_memory.py` の `--update` モードで `--memory-type` / `--importance` を指定できるよう対応
- **`format_result` に v5 フィールドを追加**: recall 結果の表示に `memory_type`, `importance`, `retention_score` を追加し、忘却リスクや記憶タイプを一目で確認できるよう改善
- **コーパス df ドリフトを修正**: `similarity.py` の `update_corpus_entry` で新規エントリ追加時に `df` と `total_docs` を更新するよう修正し、IDF値の陳腐化を防止
- **`promote_memory.py` のインデックス活用**: `load_candidate_memories` をインデックスで事前フィルタリングするよう変更し、候補ファイルのみ読み込むことでパフォーマンスを向上
- **`metadata.version` を 5.1.0 → 5.2.0 に更新**

---

## v5.1.0（2026-03-30）

### 修正

- **`generate_id` の競合リスク解消**: ディレクトリのファイル数カウントからインデックスベースの同日付ID最大値探索に変更し、削除済みファイルや他カテゴリとのID衝突を防止
- **`--scope shared` の誘導メッセージ**: `save_memory.py` で `--scope shared` を指定した際に `promote_memory.py` への誘導メッセージを表示
- **非TTY環境のデフォルト動作を明示化**: 類似記憶が見つかった際に非インタラクティブ環境では新規保存を継続することをログ出力
- **`memory_type` フィルタをインデックス段階に移動**: ランキング後の後処理からインデックス検索段階に移動し、`--limit` 件数の精度とパフォーマンスを向上（`recall_memory.py`、`search_with_index` / `search_all_scopes` / `fallback_search` に `memory_type_filter` 引数を追加）

---

## v5.0.0（🧠 脳構造インスパイア設計）

### 新機能

- **記憶タイプ分類**: episodic（海馬）/ semantic（新皮質）/ procedural（大脳基底核）の3分類
- **記憶の固定化（consolidate）**: エピソード記憶群を意味記憶に蒸留（海馬→新皮質モデル）
- **重要度レベル（importance）**: critical/high/normal/low の扁桃体モデルによる記憶保持力制御
- **忘却曲線**: エビングハウスモデルによる指数関数的減衰 + 間隔反復効果
- **文脈依存想起**: `--context` / `--auto-context` による前頭前皮質モデルの選択的活性化
- **記憶レビュー（review）**: 海馬リプレイモデルによる定期的な記憶の棚卸し
- recall 時に `retention_score` を自動更新（間隔反復効果による忘却曲線リセット）
- ハイブリッドランキング 4軸化（keyword + TF-IDF + meta_boost + context_boost）

---

## v4.0.0

### 新機能

- 記憶クラスタリング・類似記憶推薦（TF-IDF + コサイン類似度）
- save 時の重複検出・統合提案（`--dedup-threshold` で閾値調整可能）
- recall ハイブリッドランキング（キーワード + 意味的類似度 + メタデータ）
- 自動タグ抽出（TF-IDF ベース、`--no-auto-tags` で無効化可能）
- cleanup 智的化（重複検出モード・品質スコア閾値モード）
  - `--duplicates-only`: 類似度 >= 0.85 のペアを検出
  - `--quality-threshold`: 総合品質スコア < 閾値を削除候補に

---

## v3.0.0

### 変更

- share_score 算出ロジックを v3 に更新（重要度加味）
- ユーザー評価による share_score 変動ロジックを整備

---

## v2.0.0

### 変更

- インデックス（`.memory-index.json`）による高速検索を導入（2段階検索）
- スコープ設計（workspace / home / shared）を確立
- `promote_memory.py` による昇格フローを整備
