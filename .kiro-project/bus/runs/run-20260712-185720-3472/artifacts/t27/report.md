# t27 成果報告

## サマリー

実装変更は行っていない。指定 worktree の HEAD `2160ca4` には、タスク記載の
`_code_fence_lines`、`_first_executable_line`、`_has_command_like_leading_token` および
問題のフェンス外候補フィルタが存在しない。一方、指定された対象ブランチ
`kp/synth_verify-_first_comm-172544` は別系統の `380b452` まで既に進んでおり、そちらには
問題の行が存在する。git 利用規約により checkout/rebase/別 worktree への書き込みが禁止されて
いるため、対象箇所へ安全に変更を適用できなかった。

## 検証

- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
  - 8 passed, 512 deselected, 12 subtests passed（終了コード 0）
- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q`
  - 520 passed, 12 subtests passed（終了コード 0）
- システムの `/usr/bin/python3` には pytest がないため、既存 venv を PATH の先頭に置いて実行した。

## 前提・未解決事項

- フェンス内経路を変えず、フェンス外候補だけを正規化するには、対象ブランチ先端にある実装を
  ベースにする必要があると判断した。
- 対象ブランチ先端を基点にした専用 worktree の再払い出しが必要。
- commit/push は実行していない（明示された kiro-flow 自動処理規約に従った）。
- 範囲外の問題は見つけていない。
