### agent-dots: 自律タスク発見（Discovery）の詳細設計を追加

- ローカル蓄積からタスク候補を発見し、提案・承認・実行につなげる Discovery の詳細設計書（`docs/plans/2026-11-24-agent-dots-discovery-design.md`）を追加。harvest コレクタ、`.dot/discovery/` 索引、outbox 新種別 `propose`、評価プロンプト、act 拡張、proposals CLI、policy の `discovery` 節、日次 harvest DAG 例、Obsidian 公式 CLI の配線を定義。
- シーケンス図の矢印1〜19すべてについて、実現手段・データ形式・トリガ・失敗時の扱いを対応表に明記（copilot-app-jobs の終了コード3/4の扱いを含む）。
