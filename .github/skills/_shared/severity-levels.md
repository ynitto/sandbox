# 重要度レベル定義

> レビュースキル共通の severity 定義。`review-output-schema.json` の `severity` フィールドはこの定義に従う。

| レベル | 意味 | 対応方針 |
|--------|------|---------|
| `critical` | セキュリティ脆弱性・データ損失リスク・本番障害直結 | **即時修正必須**。マージをブロック |
| `high` | 機能的バグ・重大な設計違反・パフォーマンス劣化 | **修正必須**。次のイテレーションまでに対応 |
| `medium` | コード品質低下・軽微な設計問題・保守性の懸念 | **修正推奨**。技術的負債として追跡 |
| `low` | スタイル・命名・コメント不足・ベストプラクティス逸脱 | **任意**。改善できれば望ましい |
| `info` | 提案・代替案・学習ポイント | **参考情報**。対応不要 |

## verdict マッピング

各レビュースキルが使う verdict 値:

| スキル | 合格 | 不合格 |
|--------|------|--------|
| code-reviewer | `lgtm` | `request_changes` |
| security-reviewer | `pass` | `fail` |
| architecture-reviewer | `lgtm` | `request_changes` |
| design-reviewer | `lgtm` | `request_changes` |
| test-reviewer | `lgtm` | `request_changes` |
| document-reviewer | `approved` | `needs_revision` |
| sprint-reviewer | `sprint_passed` | `sprint_failed` |

## 自動判定ルール

- `critical` が 1 件以上 → 不合格
- `high` が 3 件以上 → 不合格
- `medium` のみ → スキルの判断に委ねる
- `low` / `info` のみ → 合格
