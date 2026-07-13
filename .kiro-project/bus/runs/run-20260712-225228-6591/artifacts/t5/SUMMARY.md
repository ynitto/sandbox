# 失敗1件目 根本原因分析・修正方針

対象: `classification.json` (t4成果物) の `failures[0]`
= **`GitDistributedTests.test_empty_objects_clone_is_rebuilt_on_reuse`**
(`tools/kiro-flow/tests/test_kiro_flow.py:3597`)

## (a) 成果 — 判定と修正方針

**判定: テスト側の前提誤り（実装側の欠陥ではない）**

- 失敗地点はテストヘルパー `_zero_loose_objects()`（`test_kiro_flow.py:59-72`、テスト内で
  電源断による loose object のゼロバイト化を模擬する共通関数）であり、テスト対象である
  `kiro-flow.py` の自己修復ロジック（`GitBus._probe_integrity` L1198 / `_rebuild_clone` L1211）
  には到達する前に例外が発生する。実装コードは無罪。
- 原因: git は loose object を作成時に **常に** `0444`（読み取り専用）で作る（OS 非依存の git
  標準動作。下記(b)で実測確認）。ヘルパーはこれを考慮せず `open(path, "wb").close()` で直接
  上書きしようとしており、**非 root ユーザーで実行すると** OS の権限チェックにより
  `PermissionError` になる。CI コンテナは root 実行のため権限チェックが素通りして顕在化せず、
  root 権限を持たない macOS ローカル環境でのみ露見していた（"macOS 特有" という見立ては、
  正確には「非 root 実行環境特有」）。
- **修正は最小差分で既に適用済み**: コミット `0cf9c59`（`[kiro-flow] t8`、`HEAD` の祖先。
  `git merge-base --is-ancestor 0cf9c59 HEAD` で確認済み）が
  `tools/kiro-flow/tests/test_kiro_flow.py` の `_zero_loose_objects()` 内、
  ゼロバイト切り詰め (`open(p, "wb").close()`) の直前に **1行**
  `os.chmod(p, 0o644)`（現行ファイル L69）を追加している。
  - 変更ファイル: `tools/kiro-flow/tests/test_kiro_flow.py`
  - 変更関数: `_zero_loose_objects`
  - 変更内容: 切り詰め対象パス `p` に対し `os.chmod(p, 0o644)` を書き込み直前に呼ぶ
  - 実装ファイル（`tools/kiro-flow/kiro-flow.py`）は無変更 — 妥当（バグはテストの模擬手順にあり、
    プロダクトの自己修復経路には無関係なため）
- 本 worktree には上記コミットが既に取り込まれており、**追加の差分は不要**。

## (b) 検証内容と結果

1. **現状再現テスト**: 対象テスト単体を worktree で実行 → `1 passed`
   （`python3 -m pytest tools/kiro-flow/tests/test_kiro_flow.py -q -k test_empty_objects_clone_is_rebuilt_on_reuse`）
2. **修正コミットの祖先性確認**: `git merge-base --is-ancestor 0cf9c59 HEAD` → 真
3. **リグレッション実測（fix 適用前の再現）**: scratchpad に `kiro-flow.py` / `executors/` /
   `test_kiro_flow.py` を退避コピーし、`test_kiro_flow.py` のみ `0cf9c59` の親コミット
   （fix 適用前）の内容に差し替えて同テストを単体実行 →
   `PermissionError: [Errno 13] Permission denied: .../objects/35/1ae4a8...`
   でヘルパー内 `open(...).close()` 行にて失敗することを実機で再現。worktree 本体は無編集。
4. **git のオブジェクト権限の実測**: 一時リポジトリで `commit` 後に `.git/objects/**` を
   `ls -la` → 全て `-r--r--r--`（`0444`）で作成されることを確認（uid=501, 非root）。
5. **完了条件コマンド**: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
   → `900 passed`、終了コード 0（本タスク対象の失敗1件目を含め全件green）。
6. `git status --short` で worktree 差分なしを確認（調査のみ、コード変更なし）。

## (c) 前提・未解決事項・範囲外所見

- **前提**: 「分類結果の先頭 test id」は t4 成果物 `classification.json` の
  `failures[0]`（`id: 1`）= `test_empty_objects_clone_is_rebuilt_on_reuse` と解釈した
  （t4 の `SUMMARY.md` 表の順序とも一致）。
- **t4 の一次結論を独立に再検証し確認**: `_zero_loose_objects()` 経由の権限起因、
  fix commit `0cf9c59`、テスト側原因という判定は本タスクの実機再現で裏付けが取れた。
- **未解決事項**: なし（本件は fix 適用済み・green 確認済みで完結）。
- **範囲外で見つけた問題**:
  - "macOS 特有" という現象の実体は前述の通り「非 root 実行環境特有」であり、CI が
    root 実行である限り今後も同種の権限前提ミスがローカル環境でのみ再発しうる
    （テストヘルパー設計の一般的な注意点。個別修正は本タスク範囲外のため未対応）。
  - `GitBus` と `StateGit` の自己修復ロジックの重複は t3 が既に指摘済みで、本件とは
    無関係のため触れていない。
