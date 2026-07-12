# t1: git 自己修復テスト4件の失敗ログ採取 — 調査結果

## (a) 成果サマリー

**タスクの前提（4件の失敗テストの raw ログ採取）は現時点の worktree では再現しなかった。**
`python3 -m pytest tools/kiro-flow/tests -q -rA` は **388 passed, 0 failed**（exit code 0）。
完了条件コマンド `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` も
**900 passed, 0 failed**（exit code 0）で、この時点で既に満たされている。

### 原因: 対象タスク（macOS-kiro-flow-git-4-gr-171537）の同一ブランチで、先行する worker が既に修正済みだった

作業ブランチ `kp/macOS-kiro-flow-git-4-gr-171537` の直近コミット2件が、まさに
「git 自己修復テストの macOS 特有の失敗」を修正する内容だった:

- `0cf9c59` `[kiro-flow] t8 (run-20260712-172155-3661)`
  - `tools/kiro-flow/tests/test_kiro_flow.py` の `_zero_loose_objects` ヘルパーを修正。
  - **原因**: macOS 上で git が作成する loose object ファイルは `0444`（読み取り専用）権限になるため、
    0 バイトへの `open(..., "wb")` 切り詰めが `PermissionError` で失敗していた（Linux ではデフォルト権限が
    書き込み可のため顕在化しない）。
  - **修正**: 切り詰め前に `os.chmod(p, 0o644)` を追加。
  - このヘルパーは `test_corrupt_index_clone_is_rebuilt` / `test_empty_objects_clone_is_rebuilt_on_reuse` /
    `test_sync_push_self_heals_on_object_corruption` /
    `test_state_sync_self_heals_on_object_corruption_midflight` など、git 自己修復系テスト群で共通利用されている。
- `5681a20` `[kiro-flow] t1 (run-20260712-173148-2734)`
  - `GitlabExecutorPluginTests` の `setUp`/`tearDown` で `KIRO_FLOW_DEFER_WAITS` 環境変数を
    退避・削除・復元するよう修正（残存していると `execute` が `DeferDecision` を投げてテストが壊れる）。
  - git 自己修復テストそのものではないが、同ブランチ・同ファイルへの先行修正。

両コミットの diff は本ディレクトリの `prior_fix_commit_0cf9c59.diff` / `prior_fix_commit_5681a20.diff` に採取済み。

## (b) 検証内容と結果

| コマンド | 結果 | raw ログ |
|---|---|---|
| `python3 -m pytest tools/kiro-flow/tests -q -rA` | **388 passed, 0 failed**, exit 0 | `kiroflow_pytest_-rA_raw.log` |
| `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`（完了条件コマンド） | **900 passed, 0 failed**, exit 0 | `full_suite_completion_condition_raw.log` |

git 自己修復関連と思われる全テスト（`GitDistributedTests` / `StateGitSyncTests` 配下）は
`kiroflow_pytest_-rA_raw.log` 内ですべて `PASSED`:

- `test_corrupt_index_clone_is_rebuilt`
- `test_corrupt_remote_gives_clear_diagnostic_not_reclone_loop`
- `test_empty_objects_clone_is_rebuilt_on_reuse`
- `test_interrupted_rebase_recovered_on_reuse`
- `test_stale_index_lock_recovered_on_reuse`
- `test_lock_going_stale_during_retry_is_removed`
- `test_git_retries_while_live_lock_is_held`
- `test_sync_push_self_heals_on_object_corruption`
- `test_state_sync_self_heals_on_object_corruption_midflight`
- `test_empty_objects_state_clone_is_rebuilt_on_reuse`

失敗したテスト・アサーション文言・スタックトレース・実際値/期待値は — **存在しないため採取していない**（存在しないものを捏造していない）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: タスク文言は「失敗している4件の raw ログ採取」だが、実際の worktree では0件失敗だったため、
  タスクの目的（後続の修正判断に資する情報提供）に沿って「なぜ失敗が再現しないか」の根拠（先行コミットでの修正）を
  代わりに報告した。ログを空のまま「採取できませんでした」とだけ返すより有用と判断。
- **完了条件について**: このタスク自体の完了条件として渡された
  `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` は既に exit 0 で成功している
  （このタスクの作業前から、先行 worker の修正により満たされていた）。本タスクはコード変更を伴わない調査のみで
  worktree に変更は加えていない。
- **範囲外の指摘事項**: なし。既存の2コミットの修正内容に技術的な懸念は見当たらなかった
  （`os.chmod` 追加は macOS の read-only loose object 権限への対処として妥当）。
- **ファイル変更**: なし（調査のみ、worktree は無変更）。
