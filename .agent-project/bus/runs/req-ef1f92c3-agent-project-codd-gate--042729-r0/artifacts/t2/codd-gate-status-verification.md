# t2 成果 — codd_gate_status.py の実装検証

**切り口**: 新規実装ではなく「既存実装の実行時検証」。`tools/agent-project/codd_gate_status.py` は
main ブランチに既に実装・コミット済み（t1 調査済み）だったため、他候補（コード差分の提示）とは
異なり、本成果は **実挙動を再現テストで裏取りした検証レポート** に絞る。

## (a) 成果そのもの

`tools/agent-project/codd_gate_status.py`（`/Users/nitto/Workspace/sandbox` の main ブランチ、
専用 worktree 経由で参照）は、タスクで要求された仕様を**そのまま満たす形で既に実装済み**であることを
確認した。コード変更は行っていない（変更不要と判断）。

- `detect_status(explicit=None, which=shutil.which) -> CoddGateStatus`
  - `codd_gate_status.py:119-138`
  - `usable` フラグ: `CoddGateStatus.usable`（`binary is not None and not findings`、`:44-46`）
  - `command(subcmd, *args)`: `CoddGateStatus.command()`（`:48-50`）。usable でなければ `None`、
    usable なら `[*binary, *args]`
- 実行形態の自動判別: `codd_gate_detect.resolve_codd_gate()`（`codd_gate_detect.py:39-56`）が
  `explicit 指定 → shutil.which("codd-gate")（PATH上のCLI） → tools/codd-gate/codd-gate.py（リポジトリ内
  スクリプト） → None（未導入）` の順で解決する3分岐を実装済み
- 未導入時のフォールバック: `resolve_codd_gate` が例外を投げない設計に加え、`detect_status` 側でも
  `try/except Exception` で囲み（`:134-137`）、想定外の I/O 例外（`shutil.which` 自体が例外を出す等）が
  発生しても `binary=None` に丸めて `usable=False` の `CoddGateStatus` を返す（例外を外へ漏らさない）

## (b) 検証内容と結果

git 利用規約に従い、共有チェックアウト（`/Users/nitto/Workspace/sandbox`、無関係な大規模差分あり）
には書き込まず、`git_worktree.py provision https://github.com/ynitto/sandbox.git --ref main` で
専用 worktree（クリーンな main、`git status --short` 差分なし）を取得し、その中で**読み取りと
テスト実行のみ**を行った（作業後 `release` で返却済み）。

1. 完了条件のアサーション（無変更で exit 0 を確認）:
   ```
   PYTHONPATH=tools/agent-project python3 -c '
   from codd_gate_status import detect_status
   s = detect_status()
   assert s.usable and s.command("verify", "--base", "HEAD")
   '
   → usable=True, command=['/Users/nitto/.local/bin/codd-gate', 'verify', '--base', 'HEAD']（PATH上のCLI経路）
   ```
2. 既存ユニットテスト（`tests/test_codd_gate_detect.py` + `tests/test_codd_gate_routing.py`）:
   `29 passed`。特に以下が本タスクの要求仕様と一対一対応することを確認:
   - `test_cli_absent_degrades_to_noop`（`Path.exists` と `which` を両方 mock して PATH・同梱パス
     どちらも見つからない状態を再現） → `usable=False`、`command()` は `None`、findings に
     「見つからない」理由が1件入る（未導入時の no-op 縮退）
   - `test_cli_absent_survives_unexpected_resolution_exception`（`which` に `OSError` を投げさせる
     mock） → 例外が外へ漏れず `usable=False` に縮退することを確認
   - `test_cli_present_and_version_compatible_is_usable` → PATH 経由での `usable=True` と
     `command()` の argv 組み立てを確認
3. 自前の追加確認（1点、想定通りの結果に訂正）: 当初 `which=lambda name: None` だけで「未導入」を
   再現しようとしたところ `usable=True` になり一見矛盾したが、これは本リポジトリに
   `tools/codd-gate/codd-gate.py`（同梱スクリプト）が実在するため、PATH 解決失敗時に
   「リポジトリ内スクリプト」経路へ正しくフォールバックした結果であり、**実装の不具合ではなく
   仕様通りの3分岐目の動作**であると判断した（真の「未導入」検証は上記2の
   `test_cli_absent_degrades_to_noop` が `Path.exists` も mock して正しく再現している）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- t1 の調査結果（`codd_gate_status.py` は既に main に実装済みで完了条件の該当コマンドを無変更で
  満たす）を実行し直して裏取りし、正しいと確認した。よって本タスクの成果は「新規コード」ではなく
  「検証記録」とした。要求仕様（usable フラグ・`command(subcmd, *args)`・3分岐自動判別・未導入時の
  no-op フォールバック）はすべて既存実装でカバーされており、書き換える理由がないため変更していない。
- 対象ファイルへの書き込みは自分の worktree（`.agent-project` sparse checkout）に無いため、
  git 利用規約に従い専用 worktree を provision して読み取り専用で検証した（書き込みは一切行っていない）。

**未解決事項（t3/t4・gate への申し送り、t1 メモと同一の指摘を再確認）**:
- `.agent/agent-project.yaml` への `regression_cmd:`/`intake_cmd:` の実値投入と、
  `agent_project/mr.py`（regression, `mr.py:437-438` 付近）・`agent_project/model.py`
  （intake, `run_intake`）への `codd_gate_status`/`codd_gate_base`/`codd_gate_routing` の import・
  結線は、本タスク（`codd_gate_status.py` 実装）のスコープ外であり、依然として未着手（t3/t4 が担当）。
- `agent_project/__init__.py` の合成順・名前空間衝突の要確認事項は t1 の指摘のまま未解消。

**範囲外で見つけた問題**: 新規の発見なし（t1 が既に報告した docstring 行番号の陳腐化、共有チェックアウトの
リスクを再確認したのみ）。
