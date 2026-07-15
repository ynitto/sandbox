# docs/designs 設計書 索引（worktree 参照用）

この worktree には設計書実体が無いため、実体のある `sandbox` 側ドキュメントへ相対リンクする。

1. [`agent-project-design.md`](../../../../sandbox/docs/designs/agent-project-design.md) — 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の設計正典。3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を示す。
2. [`agent-flow-design.md`](../../../../sandbox/docs/designs/agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し、複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。
3. [`codd-gate-design.md`](../../../../sandbox/docs/designs/codd-gate-design.md) — ドキュメント・コード・テストの一貫性を維持する決定的ゲート設計。agent-project 本体は無改造のまま、汎用フック契約（E1〜E3）で連携する独立ツール。
4. [`agent-tools-rename-design.md`](../../../../sandbox/docs/designs/agent-tools-rename-design.md) — 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard は移行完了、`kiro-loop → agent-loop` は未了。
