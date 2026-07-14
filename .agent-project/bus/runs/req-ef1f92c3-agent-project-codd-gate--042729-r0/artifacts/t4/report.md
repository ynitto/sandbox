# t4 成果報告 — test_codd_gate_detect.py / test_codd_gate_routing.py

**差別化の切り口**: 他候補がゼロから新規作成を試みる前提に対し、本候補は「対象2ファイルは main
ブランチに既に実装・pass 済み」という t1 の調査結果を実地で読み込み検証した上で、その中で唯一
未検証だったテスト分岐（`resolve_codd_gate` の同梱パス解決）だけを追加する最小差分アプローチを取った。

## (a) 成果そのもの

- 対象2ファイルは `/Users/nitto/Workspace/sandbox`（main ブランチ）に**既に存在**していた
  （t1 調査済み）。git 利用規約に従い共有チェックアウトには書き込まず、
  `git_worktree.py provision https://github.com/ynitto/sandbox.git --ref main` で専用の
  一時 worktree を取得し、そこで内容を精読・検証した（作業後 `release` 済み、共有チェックアウト・
  本タスクの sparse worktree のいずれにも書き込んでいない）。
- 中身を精読した結果、`test_codd_gate_routing.py`（yaml の結線からコマンド組み立てまでの
  ルーティングテスト）は `resolve_repos_arg` / `resolve_repo_dir_arg` / `build_routing_args` の
  全分岐（相対解決・絶対フォールバック・vcwd 無し・NAME=DIR 組み立て・`CoddGateStatus.command()`
  との合成）を網羅しており、追加すべき欠落は見つからなかった。**無変更で候補に採用**。
- `test_codd_gate_detect.py`（導入済み／未導入／パス差異の検出）は、`resolve_codd_gate` の
  3段解決連鎖（explicit → PATH（which）→ 同梱パス）のうち「explicit 指定」「PATH で発見」
  「両方とも無し（未導入）」の3ケースは検証済みだったが、**中間分岐「PATH には無いが同梱パスで
  発見」だけが唯一未検証**だった。これは `codd_gate_detect.py:53-55` の
  `local.exists()` 分岐に対応する実コードパスであり、「パス差異の検出」という本タスクの目的に
  照らして埋めるべき欠落と判断し、テストケースを1件追加した
  （`test_resolve_codd_gate_found_via_bundled_path_when_path_lookup_fails`）。
  差分は本ディレクトリの `test_codd_gate_detect.diff` を参照。追加後の全文は
  `test_codd_gate_detect.py` / `test_codd_gate_routing.py`（後者は無変更）として本ディレクトリに
  同梱した。

## (b) 検証内容と結果

```
$ python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py \
                     tools/agent-project/tests/test_codd_gate_routing.py -v
# → 30 passed（既存29件 + 追加1件）、失敗・エラーなし
```

既存29件・追加1件のいずれも regress していないことを確認済み（t1 が確認した「29 passed」から
「30 passed」への差分は今回追加した1件のみ）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本タスクの完了条件として「対象2ファイルが存在し、既存 pytest（29件）+ 追加検証が全て通ること」を
  採用した。t1 のメモが指摘する通り、渡された4コマンドの完了条件は run 全体（regression_cmd/
  intake_cmd の yaml 結線を含む）の DoD であり、t4 単体（このテストファイル作成）のスコープではない
  と判断し、yaml 結線・`mr.py`/`model.py` への import 配線には一切手を入れていない
  （t2/t3 の責務、範囲外）。
- 「作成する」の解釈: 対象ファイルが既に存在し高品質だったため、ゼロからの書き直しではなく
  既存実装の精読・欠落分岐の特定・最小差分での補強を「作成」の実質とみなした。

**未解決事項（gate/synth への申し送り）**:
- 他の並列候補（他の generate ワーカー）が対象2ファイルの存在に気づかず重複・非互換な内容を
  生成している可能性がある。synth 段階で「既存 main 実装 + 本候補の1件差分」を正としてマージし、
  重複コンテンツを採用しないことを推奨する。

**範囲外で見つけた問題（直していない。報告のみ、t1 の指摘と重複するため詳細は investigation-memo.md
参照）**:
- `codd_gate_detect.py`/`codd_gate_status.py` 等の docstring 内行番号参照が
  パッケージ分割後の現状と不一致（陳腐化）。
- `agent_project/mr.py`（regression 実行）・`agent_project/model.py`（intake 実行）は
  `codd_gate_status`/`codd_gate_routing` を未 import。`.agent/agent-project.yaml` にも
  `regression_cmd`/`intake_cmd` の実値が未設定（t2/t3 の担当領域）。
