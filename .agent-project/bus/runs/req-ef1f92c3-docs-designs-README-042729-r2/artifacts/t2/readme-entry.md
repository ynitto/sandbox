【切り口】既存 README を書き換える前に、まず現況（既に完了条件を満たす README が存在するか）を検証し、その上で抽出物の正しさを裏付ける。

## (a) 成果 — README 用導線文と相対リンク

- 対象ファイル: `docs/designs/agent-flow-design.md`（worktree 内に実在確認済み、69,683 バイト）
- 相対リンク: `./agent-flow-design.md`
- 抽出した要約（1〜2行）: git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。
- README 掲載形式（Markdown リンク行）:
  `[`agent-flow-design.md`](./agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。`

## (b) 検証内容と結果

1. `test -f docs/designs/agent-flow-design.md` → 実在確認 OK。
2. ファイル冒頭（1〜80行）を読み、概要節「agent-flow は、kiro-cli を頭脳にして Claude 風の Dynamic Workflow（実行時にタスク構造を動的生成 → ワーカーへ委譲 → 結果を評価して再計画 → 統合）を実現する基盤」「通信はファイルのみ。メッセージバスをローカルディレクトリにも共有 git リポジトリにもでき、後者にすると複数 PC へそのまま分散できる」と突き合わせ、上記要約が本文の主旨と一致することを確認した。
3. 完了条件（`test -f docs/designs/README.md && grep -q ... 4件`）を worktree 上でそのまま実行 → **終了コード 0 で成功**。`docs/designs/README.md` は既に存在し、「まず読むもの — 主要4設計」節に `agent-project-design.md` / `agent-flow-design.md` / `codd-gate-design.md` / `agent-tools-rename-design.md` の4件へのリンクと要約がすべて掲載済みだった。
4. 既存 README 内の agent-flow-design.md 項目（2番目のエントリ）は、上記 (a) の抽出結果と文言レベルで一致している。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスク（t2）の担当範囲は「agent-flow-design.md 単体の実在確認・要約・README用導線文/リンクの抽出」であり、README.md 全体の作成・統合は別タスク（synthesis 役）の責務と解釈した。ワークスペースが commit/push 前提の worktree であり「変更が不要（調査のみ）なら何も書き換えない」と明記されているため、README.md が既に完了条件を満たしている今回は**ファイルを書き換えなかった**。
- **未解決事項**: なし。抽出対象・完了条件とも満たされていることを確認済み。
- **範囲外で見つけた問題**: なし。README.md の「主要4設計」節の記述内容・リンクは目視確認した範囲で整合していた。
