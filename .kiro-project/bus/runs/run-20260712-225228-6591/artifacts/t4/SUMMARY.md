# t4: macOS git自己修復テスト4件の要因分類

## 成果サマリー

t1/t2 の成果物（先行修正コミット2件・macOS環境事実）に加え、対象ブランチのテストソース
`tools/kiro-flow/tests/test_kiro_flow.py` を直接確認し、`_zero_loose_objects()` の呼び出し箇所を
grep で機械的に特定した（4箇所）。この4箇所が「macOSで失敗していた git 自己修復テスト4件」に一致する。

| # | テスト | 主因 | 副因 |
|---|---|---|---|
| 1 | `GitDistributedTests.test_empty_objects_clone_is_rebuilt_on_reuse` | class=d | なし |
| 2 | `GitDistributedTests.test_sync_push_self_heals_on_object_corruption` | class=d | なし |
| 3 | `StateGitSyncTests.test_empty_objects_state_clone_is_rebuilt_on_reuse` | class=d | なし |
| 4 | `StateGitSyncTests.test_state_sync_self_heals_on_object_corruption_midflight` | class=d | なし |

**主因の根拠**: 4件すべてが共通ヘルパー `_zero_loose_objects()`（`open(path, "wb").close()` で loose
object を0バイト切り詰め）経由で破損を模擬している。macOS では git が loose object を `0444`（読み取り
専用）で作成するため、書き込みオープンが `PermissionError` になる（t2 が実機で確認済みの環境事実と一致）。
修正はコミット `0cf9c59`（`os.chmod(p, 0o644)` を切り詰め前に追加）で、この1箇所の修正が4件すべてに
効いている。よって主因はカテゴリ **(d) ファイルモード/権限** に一意に分類でき、4件とも副因なし
（パス解決・デフォルトブランチ名・BSD系コマンド差異のいずれも関与を裏付ける証拠がコード上に見当たらない）。

## t1入力との不一致（訂正）

t1 の SUMMARY.md は共通ヘルパー使用テストとして
`test_corrupt_index_clone_is_rebuilt` / `test_empty_objects_clone_is_rebuilt_on_reuse` /
`test_sync_push_self_heals_on_object_corruption` / `test_state_sync_self_heals_on_object_corruption_midflight`
の4件を挙げていたが、**`test_corrupt_index_clone_is_rebuilt` は誤り**。実コードを確認したところ、この
テストは `.git/index` に直接 `b"broken"` を書き込んで破損を模擬しており、`_zero_loose_objects()` を
呼んでいない（該当行 `tools/kiro-flow/tests/test_kiro_flow.py:3517`）。`_zero_loose_objects(` の呼び出し
箇所は同ファイル内に4箇所のみ（L3604, L3621, L4656, L4680）であり、4件目は
`test_empty_objects_state_clone_is_rebuilt_on_reuse`（StateGitSyncTests）である。本分類ではこちらを
対象4件目として採用した。

`test_corrupt_index_clone_is_rebuilt` はindex破損経路であり、loose objectの権限問題とは別の障害モードのため、
今回の「macOS特有の失敗4件」には含めなかった（index破損の再現・修正要否は本タスクの範囲外）。

## 除外した先行修正

コミット `5681a20`（`KIRO_FLOW_DEFER_WAITS` 環境変数残存の修正）は同一ブランチ・同一ファイルへの
先行修正だが、対象は `GitlabExecutorPluginTests`（git自己修復テストではない）であり、分類対象の4件には
含めていない。

## 採用した前提・未解決事項

- **前提**: 「4件の失敗」は t1/t2 が参照した macOS 特有の git 自己修復テスト失敗を指すとし、コードの
  実際の呼び出し箇所（grep 結果）を一次証拠として採用した。t1 のテスト名列挙は二次証拠として扱い、
  矛盾箇所はコード直接確認で上書きした。
- **未解決事項**: なし。作業ツリーへの変更は行っていない（本タスクは分類のみ）。
