【切り口】t1〜t3（engine 3件）が個々のツールの機能要約であるのに対し、本書は「他のどの設計書を読んでいても kiro-*/agent-* の二重命名に遭遇したときの解読表」という、README 全体を横断するメタ文書としての役割を軸に抽出する。

## (a) 成果 — README 用導線文と相対リンク

- 対象ファイル: `docs/designs/agent-tools-rename-design.md`（worktree 内に実在確認済み、4,212 バイト、章立ては `## 1. 目的`〜`## 6. 非目標` の6節）
- 相対リンク: `./agent-tools-rename-design.md`
- 抽出した要約（1〜2行）: 旧 `kiro-*` 系統（kiro-project / kiro-flow / kiro-projects-viewer / kiro-loop）を `agent-*`（agent-project / agent-flow / agent-dashboard / agent-loop）へクローン移行・改称する方針と新旧名称対応表。移行完了後に旧系統を削除する前提で、`kiro-loop → agent-loop` のみ現時点で未了。
- README 掲載形式（Markdown リンク行）:
  `[`agent-tools-rename-design.md`](./agent-tools-rename-design.md) — 旧 kiro-* 系統を agent-* へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了、kiro-loop → agent-loop のみ未了。`docs/designs/` 内で kiro-*/agent-* の二重命名に出会ったら本書が解読表になる。`

## (b) 検証内容と結果

1. `test -f docs/designs/agent-tools-rename-design.md` → 実在確認 OK（worktree `/var/folders/.../sandbox` 直下、読み取りのみ）。
2. 全文（85行）を読み、「§1 目的」「§4 設計書の扱い」の新旧設計書対応表と、「§6 非目標」に明記された `kiro-loop の移行・削除は対象外` を突き合わせ、上記要約の「kiro-loop → agent-loop のみ未了」という記述が本文と矛盾しないことを確認した。
3. 完了条件（`test -f docs/designs/README.md && grep -q ... agent-project-design.md / agent-flow-design.md / codd-gate-design.md / agent-tools-rename-design.md`）を worktree 上でそのまま実行 → **終了コード 0 で成功**。`docs/designs/README.md` は既に存在し、4件へのリンクと要約が「まず読むもの」節・カテゴリ別索引の両方に掲載済みだった。
4. 既存 README 内の該当エントリ（4番目、および「2. ループ拡張」節の冒頭注記）を読み合わせたところ、`kiro-loop → agent-loop` 未了という事実関係は本抽出結果と一致していた。既存記述は「ループ拡張節への橋渡し」まで踏み込んでおり、本抽出の「メタ文書（解読表）」という切り口を追加すれば synth 側の統合材料として補強になると判断した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスク（t4）の担当範囲は「agent-tools-rename-design.md 単体の実在確認・要約・README 用導線文/リンクの抽出」であり、README.md 全体の作成・統合は synth タスクの責務と解釈した。ワークスペースは commit/push 前提の worktree であり、指示に「変更が不要（調査のみ）なら何も書き換えない」とあるため、README.md が既に完了条件を満たしている今回は**ファイルを書き換えなかった**。
- **未解決事項**: なし。抽出対象・完了条件とも満たされていることを確認済み。
- **範囲外で見つけた問題**: なし。既存 README の「2. ループ拡張」節の記述（`kiro-loop-*` が現行、`agent-loop-*` は未統合クローン）は本書 §6「非目標」の記載と整合しており、乖離は見当たらなかった。
