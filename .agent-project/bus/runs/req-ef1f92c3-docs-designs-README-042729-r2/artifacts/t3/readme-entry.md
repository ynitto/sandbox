# t3: codd-gate-design.md 導線抽出

**差別化の切り口**: 要約を設計書の自己申告だけで作らず、設計書が参照する実装ディレクトリ
（`tools/codd-gate/`）が実在するかを突き合わせ、「唯一の設計正典」表記の裏取りをしたうえで
導線文を確定する。

## (a) 成果 — README 用導線文と相対リンク

- 対象ファイル: `docs/designs/codd-gate-design.md`（worktree 内に実在確認済み、349 行）
- 相対リンク: `./codd-gate-design.md`
- 抽出した要約（1〜2行）: ドキュメント・コード・テストの一貫性を「受け入れ前の差分ゲート」と
  「負債の棚卸し→タスク化」で常時維持する決定的ツール codd-gate の設計正典。
  agent-project 本体には手を入れず、`schemas/` の共通データ契約と agent-project 側の汎用フック
  （regression_cmd / intake_cmd / acceptance）だけで結線する完全独立ツールという位置づけを持つ。
- README 掲載形式（Markdown リンク行）:
  `3. [`codd-gate-design.md`](./codd-gate-design.md) — ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツールの設計正典。agent-project 本体は無改造のまま、`schemas/` の共通データ契約と agent-project 側の汎用フック契約（E1〜E3）の2点で連携する独立ツール。`

## (b) 検証内容と結果

1. `test -f docs/designs/codd-gate-design.md` → 実在確認 OK（349 行）。
2. ファイル冒頭（1〜80行）を読み、「codd-gate は、ドキュメント・コード・テストの一貫性を
   『受け入れ前のゲート』と『負債の棚卸し→タスク化』で常時維持する決定的ツール」「agent-project に
   依存しない独立ツール（依存は python3 と git のみ）」「常駐（長期実行）は agent-project 側だけが持ち、
   codd-gate のサブコマンドはすべて単発・有界」という記述を確認し、上記要約が本文の主旨と一致することを
   確かめた。
3. 設計書冒頭が「関連: `tools/codd-gate/`」と自己申告する実装ディレクトリの実在を
   `ls tools/codd-gate/` で確認 → `README.md` / `codd-gate.py` / `install.sh` / `tests` が揃っており、
   「単体でも完結して使える」という設計書の主張と実装配置が矛盾しないことを裏取りした。
4. 完了条件（`test -f docs/designs/README.md && grep -q ... 4件`）を worktree 上でそのまま実行 →
   **終了コード 0 で成功**。`docs/designs/README.md` は既に存在し、「まず読むもの — 主要4設計」節
   3番目に `codd-gate-design.md` の要約・相対リンクが掲載済みで、上記 (a) の抽出結果と文言レベルで
   一致している。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスク（t3）の担当範囲は「codd-gate-design.md 単体の実在確認・要約・README用導線文/
  相対リンクの抽出」であり、README.md 全体の作成・統合は別タスク（synthesis 役）の責務と解釈した。
  ワークスペースの指示に「変更が不要（調査のみ）なら何も書き換えない」と明記されているため、
  README.md が既に完了条件を満たしている今回は**ファイルを書き換えなかった**。
- **未解決事項**: なし。抽出対象・完了条件とも満たされていることを確認済み。
- **範囲外で見つけた問題**: なし。README.md の該当エントリおよび `tools/codd-gate/` の実装配置は
  目視・実在確認した範囲で設計書の記述と整合していた。機密情報は含めていない。
