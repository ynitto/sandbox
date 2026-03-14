# メトリクス操作

→ 実装: `scripts/metrics_report.py`, `scripts/metrics_collector.py`

## サブ操作

| サブ操作 | コマンド | 説明 |
|---|---|---|
| **metrics** | `python metrics_report.py` | 全スキルのサマリテーブル |
| **metrics-detail** | `python metrics_report.py --skill <name> --detail` | 特定スキルの詳細（週次チャート付き） |
| **metrics-co** | `python metrics_report.py --co-occurrence` | スキル共起マトリクス（上位10ペア） |
| **metrics-collect** | `python metrics_collector.py` | JSONL ログを再集計してレジストリ更新 |
| **metrics-collect（期間指定）** | `python metrics_collector.py --days 30` | 直近 N 日のみ集計 |
| **metrics-collect（ローテーション）** | `python metrics_collector.py --rotate` | 90 日超の古いログを `.bak` へアーカイブ |
| **metrics（Markdown出力）** | `python metrics_report.py --output metrics-report.md` | Markdown ファイルへ出力 |

## データフロー

```
record_feedback.py --duration --subagent-calls --co-skills
        │
        ▼
~/.agent-skills/metrics-log.jsonl  ← 生イベント（JSONL 追記）
        │
        ▼  metrics_collector.py
registry.json .metrics        ← サマリ（インクリメンタル + バッチ更新）
        │
        ▼  metrics_report.py
ターミナル / Markdown          ← 可視化レポート
```
