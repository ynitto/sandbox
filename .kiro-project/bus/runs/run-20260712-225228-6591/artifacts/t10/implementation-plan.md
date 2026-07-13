# 統合実装計画（macOS-kiro-flow-git-4-gr-171537）

## 1. 統合判定

t5/t6/t7/t8（検証）＋ t9（独立検算・verify=pass）の4系統の成果は**単一の根本原因**に収束しており、矛盾なし。修正は**コミット `0cf9c599671d89bdb1f967d766dc5c5002bb0bd9` として作業ブランチに既に適用済み**。本タスクでの独立再実行でも worktree 差分なし・完了条件コマンド `900 passed`（exit 0）を確認した。追加のコード変更は不要。

## 2. 共通根本原因（重複排除後）

対象4件はすべて**同一ヘルパー関数の同一欠陥**が原因であり、実装側自己修復ロジック（`GitBus`/`StateGit`）には到達すらしていなかった。個別の実装バグではない。

- **箇所**: `tools/kiro-flow/tests/test_kiro_flow.py:59` `_zero_loose_objects(clone)`
- **欠陥**: loose object を `open(p, "wb").close()`（修正前は chmod なしで直接オープン）で0バイト切り詰めする際、非root実行環境（macOSローカル等。CIはroot実行のため顕在化しない）では git が loose object を `0444`（読み取り専用）で作成しているため `PermissionError` が発生し、破損模擬の前段でテスト自体が失敗する。
- **分類**: category d（ファイルモード/権限）。テスト側の前提誤り。実装は無罪。

## 3. 対象4テストとコールサイトの対応（重複統合済み）

`_zero_loose_objects()` の呼び出し箇所は4つで、classification.json（t4）記載の失敗id 1〜4と1:1対応する。

| id | テスト | 呼び出し箇所 | 対応する t 番号 |
|----|--------|--------------|-----------------|
| 1 | `GitDistributedTests.test_empty_objects_clone_is_rebuilt_on_reuse` | test_kiro_flow.py:3604 | t5 |
| 2 | `GitDistributedTests.test_sync_push_self_heals_on_object_corruption` | test_kiro_flow.py:3621 | t6 |
| 3 | `StateGitSyncTests.test_empty_objects_state_clone_is_rebuilt_on_reuse` | test_kiro_flow.py:4656 | t7 |
| 4 | `StateGitSyncTests.test_state_sync_self_heals_on_object_corruption_midflight` | test_kiro_flow.py:4680 | t8（フルスイート実行担当） |

4件とも**修正対象は共通ヘルパー1関数のみ**であり、呼び出し側4箇所の個別修正は不要（重複修正の排除）。

## 4. 変更対象ファイル・関数ごとの最小差分

| ファイル | 関数 | 変更内容 | 状態 |
|----------|------|----------|------|
| `tools/kiro-flow/tests/test_kiro_flow.py` | `_zero_loose_objects(clone)`（59行目〜） | 69行目に `os.chmod(p, 0o644)` を1行追加（`open(p, "wb").close()` の直前） | **適用済み**（コミット `0cf9c59`） |

呼び出し側（3604/3621/4656/4680行目）・実装側（`kiro-flow.py` の `GitBus`/`StateGit` 自己修復ロジック）への変更は**なし**。

## 5. 適用順序

1. `_zero_loose_objects()` に `os.chmod(p, 0o644)` を追加 — **適用済み・追加作業なし**
2. 以降のステップなし（呼び出し側4箇所は共通ヘルパー修正で自動的に解消するため個別対応不要）

## 6. 矛盾の解消

t4 の `classification.json` に記録の通り、t1 の SUMMARY.md は失敗3件目を `test_corrupt_index_clone_is_rebuilt` と誤同定していた。t4 で `StateGitSyncTests.test_empty_objects_state_clone_is_rebuilt_on_reuse` に訂正済みであり、t7 もこの訂正後のテストを対象に検証している。`test_corrupt_index_clone_is_rebuilt` は `.git/index` への直接バイト書き込みで破損を模擬する**別の障害モード**であり、`_zero_loose_objects()` を使わないため本4件の対象外・範囲外（t3/t4/t7と一致）。本統合ではt4の訂正後の対応表を正とした。

## 7. 検証結果（本タスクでの独立再実行込み）

- worktree: `git status --short` 差分なし（クリーン）
- 対象4テスト: t5/t6/t7で個別確認済み（各 1 passed）
- 完了条件コマンド `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` を本タスクで再実行: **900 passed, exit code 0**（t8/t9の結果と一致）

## 8. 範囲外（本タスクでは対応しない）

- `GitBus`/`StateGit` の重複実装整理（t3/t4/t6/t7が範囲外と判定済み）
- `test_corrupt_index_clone_is_rebuilt`（別障害モード、上記6節参照）
- "macOS特有"問題の一般化対応（実体は非root実行環境特有。CIはroot実行のため顕在化しないが、個別対応は本タスク範囲外）

## 9. 結論

4件の修正方針はすべて同一の最小差分（`_zero_loose_objects()` への `os.chmod(p, 0o644)` 1行追加）に収束し、既にコミット `0cf9c59` として作業ブランチに適用済み。追加のコード変更なしで完了条件を満たしている。
