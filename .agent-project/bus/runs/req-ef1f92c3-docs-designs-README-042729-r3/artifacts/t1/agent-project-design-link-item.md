# タスク t1 成果物: agent-project-design.md の README リンク項目

## (a) 成果そのもの

`docs/designs/agent-project-design.md` を読み取り、README 用のリンク項目を以下のとおり確定した。

- 相対リンク（`docs/designs/README.md` 起点）: `[`agent-project-design.md`](./agent-project-design.md)`
- 一行要約: 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の設計正典。3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を地図として示す。

この項目は既に `docs/designs/README.md` の「まず読むもの — 主要4設計」節の 1 番目、および「1. 主要4設計」節に反映済みであることを確認した（後述の検証で確認）。

## (b) 検証内容と結果

1. **ファイル実在確認**: `test -f docs/designs/agent-project-design.md` → 存在（1042 行）。
2. **相対パス正確性**: `docs/designs/README.md` から見て `./agent-project-design.md` は同一ディレクトリの実ファイルを正しく指す。
3. **要約の正確性**: `agent-project-design.md` 冒頭（§0〜§1）を読み、「単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ差し戻す制御層」「3 層・2 つのループ（上位ループ＝project層／外側＝run正準ループ／内側＝agent-flow実行層）」という記述と、README 記載の要約が一致することを確認した。
4. **完了条件コマンドの実行**（このタスク単体の担当範囲を超えるが、成果の位置づけ確認のため実行）:
   ```
   test -f docs/designs/README.md && grep -q 'agent-project-design.md' docs/designs/README.md \
     && grep -q 'agent-flow-design.md' docs/designs/README.md \
     && grep -q 'codd-gate-design.md' docs/designs/README.md \
     && grep -q 'agent-tools-rename-design.md' docs/designs/README.md
   ```
   → 終了コード 0（PASS）。`docs/designs/README.md` は既に他タスクの成果として存在し、上記 4 件すべてを含む。
5. **ワークツリー状態確認**: `git status --short docs/designs/README.md` は差分なし（既にコミット済み）。本タスクでの追加編集は不要と判断し、ファイルは変更していない。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**前提**:
- 本タスクの担当は `agent-project-design.md` 1 件分のリンク項目作成と実在確認。README.md 全体の組み立ては別タスク（統合タスク）の責務と解釈した。
- README.md が既に存在し、要求されたリンク項目を含んだ状態で完了条件を満たしていたため、追加のファイル編集は行わなかった（範囲外の書き換えを避けるため）。

**範囲外で見つけた問題（修正はしていない。統合/評価タスクの判断に委ねる）**:
- `docs/designs/README.md` は「`docs/designs/` 配下の設計書 24 件」と明記しているが、実際に `docs/designs/*.md`（README.md 自身を除く）は 27 件存在する。
- README に未掲載の実在ファイルが 3 件ある: `agent-dashboard-kiro-loop-terminal-design.md` / `agent-dashboard-project-ux-improvements.md` / `agent-flow-self-healing-retry-design.md`。
- README に記載されているリンク先はすべて実在し、ファイル名の誤り・幽霊リンクはなかった（README 記載 vs 実ファイルの差分は上記の「未掲載3件」のみ）。

**未解決事項**: なし（本タスクのスコープにおいては完了）。
