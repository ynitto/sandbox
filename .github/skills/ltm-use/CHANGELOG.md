# CHANGELOG

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
