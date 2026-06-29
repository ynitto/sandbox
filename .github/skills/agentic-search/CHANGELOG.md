# CHANGELOG

## 目次

- [v1.0.0](#v100-2026-06-28)

## v1.0.0（2026-06-28）

### Added

- **新規スキル**。検索を「単発の retrieve」から「エージェントが反復する探索ループ」へ
  引き上げる検索系スキル横断の共有スキルとして新設。反復ループ（計画→検索→評価→
  再構成→展開→統合）の正典を提供し、コーパスごとの検索は各スキルに残す。
- **`scripts/hints.py`**: バックエンド非依存のヒントエンジン。正規化済み結果リストと
  クエリから `next_action`（synthesize/refine/expand/broaden）、`suggested_queries`、
  `related_ids`、`gap_keywords`、`sufficient` を計算する。ライブラリ（`compute_hints` /
  `format_hints`）と CLI（stdin/`--input` JSON → hints JSON、`--text` で人間可読）の両対応。
- **`SKILL.md`**: 設計思想・役割分担・正規化済み結果の契約・利用方法（import / CLI）・
  hints 出力スキーマ・乗り入れ済みスキル一覧を記載。
- **`references/protocol.md`**: ヒント計算・next_action 決定ロジック・反復ループ疑似コード・
  収束条件・探索中の追跡抑制（副作用バックエンド向け）を文書化。
- **初期乗り入れ**: ltm-use（`recall_memory.py`）、wiki-use（`wiki_query.py search`）、
  moltbook-use（`moltbook.py search`）に `--json` / `--suggest` を追加。
