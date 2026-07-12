# macOS git 自己修復4テストの一次調査

## サマリー

対象4件は次のテストと解釈した。いずれも `_zero_loose_objects()` で Git loose object を 0 byte にする障害注入を共有する。

- `GitDistributedTests::test_empty_objects_clone_is_rebuilt_on_reuse`
- `GitDistributedTests::test_sync_push_self_heals_on_object_corruption`
- `StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse`
- `StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight`

一次判定は **実行順序・グローバル状態ではなく、macOS 上のファイル権限差を踏んだテスト障害注入ヘルパーの脆弱性**。自己修復本体の不具合を示す証拠は得られなかった。現 HEAD には、切り詰め前に loose object を `chmod 0644` する既存修正（commit `0cf9c599671d89bdb1f967d766dc5c5002bb0bd9`）が含まれ、4件と全スイートは green。

## 一次証拠

1. `rg -n "_zero_loose_objects\\(" tools/kiro-flow/tests/test_kiro_flow.py` により、ヘルパーの呼び出しは上記4件だけ（行 3604, 3621, 4656, 4680）。
2. macOS（Darwin 25.5.0 arm64）で最小 Git repository を作り loose object の mode を取得すると全3 object が `0444`。修正前と同じ `open(object, "wb")` は `PermissionError: [Errno 13] Permission denied` を再現した。
3. 現在のヘルパーは `os.chmod(p, 0o644)` 後に切り詰める。この変更だけを含む既存 commit `0cf9c59` の diff は、OS 固有の失敗点が自己修復ロジックではなくテスト用障害注入にあることと整合する。
4. 4件を通常順で一括実行: `4 passed in 2.40s`。逆順で一括実行: `4 passed in 2.42s`。各テストを別 pytest process で単独実行: 4/4 passed（0.57s, 0.63s, 0.64s, 0.70s）。順序・同一 process の共有状態への依存を示す再現はない。
5. GitHub Actions の最新履歴には Linux runner の成功 run（例: run 28053918064）があるが、対象4件を実行したログ／workflow は確認できなかった。したがって「同じ4件が Linux/CI green」は依頼文の前提として採用し、今回取得した証拠とは区別する。

## 検証

- 完了条件: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
  - exit code 0
  - `900 passed in 114.44s`
- 対象4件、通常順: exit code 0、4 passed
- 対象4件、逆順: exit code 0、4 passed
- 対象4件、各別 process: 全て exit code 0
- `codd-gate verify --base HEAD --json`: exit code 0、差分なし
- worktree の `git status --short`: clean。調査タスクのためソース変更なし。

## 採用した前提・未解決事項・範囲外

- 「git 自己修復テスト4件」は `_zero_loose_objects()` を呼ぶ4件を指すと解釈した。
- Linux/CI の同一 commit・同一4 node ID の実行ログはリポジトリ／公開 Actions 履歴から確認できず、Linux green は元タスクの明示前提として扱った。厳密なクロスOS再確認には Linux runner/container で4 node IDを実行する必要があるが、このホストには Docker がない。
- 現 HEAD には別件の環境変数漏れ対策 commit `5681a20` もあるが、対象4件の単独・順序変更結果から、本件原因とは判定していない。
