# t6 synthesize report

## 成果

`docs/designs/README.md`（`ap/docs-designs-README-042729` ワークツリー内）を統合・改訂した。

### 統合方針と矛盾解消

- t1〜t4 は「README.md は既に主要4設計へのリンクを含み完了条件を満たしている」と報告し、いずれも追加編集をしていなかった。この点に矛盾はなく、4件のリンク項目（agent-project-design.md / agent-flow-design.md / codd-gate-design.md / agent-tools-rename-design.md）はそのまま維持した。
- t1・t5 がそれぞれ独立に「README の件数表記『24件』が実ファイル数と食い違い、未掲載の設計書が3件ある」と指摘していた（t1: 存在報告のみ、t5: 具体的に3件を特定し検証を fail 判定）。両者の指摘は一致しており矛盾はないため、本タスクでこの欠落を解消した。
- 追加した3件（t5 の指摘に基づき本文を読んで要約を作成）:
  1. `agent-dashboard-kiro-loop-terminal-design.md` — agent-dashboard から WSL 上の kiro-loop tmux セッションを覗く読み取り専用端末ビューの設計。カテゴリ「3. 実装・運用設計」に `agent-dashboard-feature-split-design.md` の直後へ配置（同系統のagent-dashboard設計として近接させた）。
  2. `agent-dashboard-project-ux-improvements.md` — agent-dashboard × agent-project 連携の UX 改善案（G1〜G6のギャップ分析）。同カテゴリに1と併せて配置。
  3. `agent-flow-self-healing-retry-design.md` — agent-flow の transient 障害向け自己回復リトライ設計（実装済み）。同カテゴリの `agent-flow-retry-inheritance-design.md` の直後へ配置（同系統のagent-flow設計として近接させた）。
- 件数表記を全箇所で 24→27 に修正し、末尾「前提・スコープ外の事項」に当初24件だった理由と本改訂で3件追加した経緯を明記した（後から読む人が差分の理由を追えるように）。

## 検証

1. 完了条件コマンド（4件の grep + test -f）: 終了コード 0（PASS）
2. 実ファイル一覧（`docs/designs/*.md` からREADME自身を除く、27件）と README 内リンク一覧（`./*.md` 抽出、27件）を機械的に diff → 差分ゼロ。幽霊リンクなし・掲載漏れなしを確認（t5 が fail 判定した観点も本改訂で解消済み）
3. `git status --short` は空ではない想定だったが、ワークツリー確認時点で他タスクとの並行編集の影響を受けていないことを確認済み（本タスクの編集のみが差分として存在）

## 前提として採用したこと

- t1〜t4 の「4リンクは既存で足りている」という判断はそのまま採用し、リンク項目自体の文言は変更していない（重複修正を避けるため）
- t5 が fail 判定した「導線漏れ」の指摘を積み残しにせず、synthesize の責務として本タスクで解消した（本 run の元要求「主要設計への導線を通す」の趣旨に、掲載漏れの解消も含まれると判断した）
