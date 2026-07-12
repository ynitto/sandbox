# t7: 失敗3件目の根本原因・分類・修正方針

## 対象

t4 の classification.json（id=3）に基づき、「失敗3件目」を以下と確定した:

- **テスト**: `StateGitSyncTests.test_empty_objects_state_clone_is_rebuilt_on_reuse`
- **ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`

## (a) 成果 — 根本原因・分類・修正方針

### 根本原因

対象テストは共通ヘルパー `_zero_loose_objects()`（`tools/kiro-flow/tests/test_kiro_flow.py:59-72`）を呼び、
`.git/objects/xx/yy` の loose object を `open(p, "wb").close()` で 0 バイトに切り詰めて「電源断による破損」
を模擬する。macOS の git は loose object を **`0444`（読み取り専用）** で作成するため、書き込みオープンが
`PermissionError` になり、破損模擬そのものが失敗してテストが落ちる。

これは `tools/kiro-flow/kiro-flow.py` 側（`StateGit` 等の自己修復実装、t3 インベントリ参照）の欠陥では
なく、**テストヘルパーが「loose object は書き込み可能」という誤った前提を置いていたこと**が原因。
実装コード（`_probe_integrity` / `_rebuild` / `sync` 等）は破損検知後の作り直しロジックとして正しく動作し、
破損を注入できてさえいれば意図通り機能する。

### 判定: テスト側の前提誤り（実装側の欠陥ではない）

根拠:
1. 影響範囲が `_zero_loose_objects()` を呼ぶ4箇所（L3604, L3621, L4656, L4680）に限定され、いずれも
   同一ヘルパーの呼び出し元 — 実装側の共有ロジックに単一の欠陥があるパターンとは形が異なる。
2. 実装側 (`kiro-flow.py`) の自己修復ロジック（`_probe_integrity`/`_is_corrupt_error`/`_rebuild`等）は
   破損したリポジトリに対して正しく作り直しを行っており、破損注入さえ成功すれば通る設計になっている。
3. failure modeは「破損を注入するテストのセットアップコード」が環境依存の権限モデル（macOSのgit実装が
   loose objectを0444で作る）を考慮していなかったために発生しており、プロダクションコードのバグではない。

### 修正方針（最小差分）

- **変更ファイル**: `tools/kiro-flow/tests/test_kiro_flow.py`
- **変更関数**: `_zero_loose_objects(clone)`（L59-72）
- **変更内容**: 各 loose object を切り詰める直前に `os.chmod(p, 0o644)` を追加し、書き込み権限を
  明示的に付与してから `open(p, "wb").close()` する。

```diff
             for name in os.listdir(d):
-                open(os.path.join(d, name), "wb").close()   # 0 バイトへ切り詰め
+                p = os.path.join(d, name)
+                os.chmod(p, 0o644)  # macOS: git が 0444 で作る loose object に書き込み権限を付与
+                open(p, "wb").close()   # 0 バイトへ切り詰め
                 zeroed += 1
```

ヘルパーが4テスト共通のため、この1箇所の修正で失敗3件目を含む対象4件全てに効く（t4 の分類と一致）。

## 状況確認: 本 worktree では既に修正適用済み・完了条件は既に green

作業ツリー（`tools/kiro-flow/tests/test_kiro_flow.py:59-72`）を確認したところ、上記と全く同一の修正が
**既にコミット済み**であることを確認した:

```
commit 0cf9c599671d89bdb1f967d766dc5c5002bb0bd9
    [kiro-flow] t8 (run-20260712-172155-3661)
```

このコミットは本ブランチ（`kp/macOS-kiro-flow-git-4-gr-171537`）の分岐元より前、過去の別run
（`run-20260712-172155-3661` の t8）で main に取り込まれ、本ブランチにも含まれている。
そのため **本タスクでの追加のコード変更は不要** と判断し、作業ツリーには一切書き込みを行っていない
（`git status --short` で差分なしを確認済み）。

## (b) 検証内容と結果

1. 対象テスト単体実行:
   `python3 -m pytest tools/kiro-flow/tests/test_kiro_flow.py -q -k test_empty_objects_state_clone_is_rebuilt_on_reuse`
   → **1 passed**
2. 完了条件コマンドのフル実行:
   `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
   → **900 passed, exit code 0**（実行時間 126.54s、Darwin/Python 3.14.2、本 worktree）
3. `git status --short` で作業ツリー差分なしを確認（本タスクはコード変更を行っていない）。

完了条件（`python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` が exit code 0）は
**既に満たされている**。

## (c) 採用した前提・未解決事項・範囲外所見

**採用した前提**:
- 「失敗3件目」は t4 の `classification.json`（`failures[].id == 3`）に対応する
  `StateGitSyncTests.test_empty_objects_state_clone_is_rebuilt_on_reuse` とした
  （t4 が指摘した通り、依頼元の一次資料 t1 の4件目テスト名 `test_corrupt_index_clone_is_rebuilt` は
  誤りで、実コード grep により訂正済みの値を正とした）。
- 「実装側の欠陥かテスト側の前提誤りか」の判定基準として、修正箇所がプロダクションコード
  （`kiro-flow.py`）かテストコード（`test_kiro_flow.py`）かで一次判定し、加えて実装ロジックが
  「正しい入力（破損注入成功）」に対して意図通り動くかを確認した。

**未解決事項**: なし（完了条件は既に成立、追加修正不要）。

**範囲外で見つけた問題**:
- t3 が既に指摘済みだが、`GitBus` と `StateGit` はほぼ同型の自己修復ロジック（ロック除去・破損検知・
  中断rebase回復・ブランチ作成フォールバック）を重複実装している。本タスクの範囲外のため変更していない。
- `test_corrupt_index_clone_is_rebuilt`（`.git/index` への直接バイト書き込みで破損を模擬）は
  `_zero_loose_objects()` を使わない別の障害モードであり、今回の「macOS特有4件」には含まれない
  （t4 の所見と一致）。本タスクでは追加調査していない。
