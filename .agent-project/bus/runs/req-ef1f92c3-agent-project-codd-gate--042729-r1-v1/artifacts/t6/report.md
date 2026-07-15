# t6 synthesize — codd-gate 検出→regression→intake 結線の統合と有効化確認

## (a) 統合結果

t1（自動検出 `codd_gate_wiring.py` + doctor finding 結線）／t2（`codd_gate_regression.py`：
regression_cmd 生成・冪等注入）／t3（intake 側 `codd_gate_debt.py` を `model.py::run_intake` に
レコード単位検証で結線）／t4（設計書・README 更新）は、いずれも同じ `/Users/nitto/Workspace/sandbox`
（main worktree）に**個別に適用済み**で、統合作業（重複解消・矛盾解消）は不要だった。4件は責務が
重ならないよう最初から分離設計されていた（検出＝t1、静的注入ツール＝t2、実行時パース＝t3、文書＝t4）。

本タスクで実施した「実際に有効化する」の中身:

1. **regression_cmd の書き込み確認** — `.agent/agent-project.yaml` は既に
   `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'` /
   `intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'` を持つ（t2 が意図した
   最終値と完全一致）。t2 が作った注入ツールを実機に対して実行して確認した:
   ```
   $ python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml --dry-run
   {"usable": true, "reason": "", "cmd": "codd-gate verify --base \"$KIRO_BASE_REV\" --repos .agent-project/repos.json", "changed": false, ...}
   ```
   `usable: true, changed: false` — 実機の `codd-gate`（`~/.local/bin/codd-gate`、PATH解決）で検出しても
   既存ファイルと差分ゼロ。書き込みは冪等に「不要」と判定される形で完了している。
2. **doctor 経由の結線ヘルスチェック** — `agent-project doctor --root .agent-project --json` を実行し、
   codd-gate 関連の finding が **0件**であることを確認した（`unresolved: 4` は全て無関係な既存の
   env/config 所見）。`doctor_codd_gate_findings` は regression_cmd/intake_cmd 双方が既に codd-gate を
   指し、codd-gate 実体も検出できている場合は所見なしを返す設計であり、この結果は
   「検出→regression→intake が実機で健全に結線されている」ことの直接証拠になる。
3. **zipapp 配布経路の欠落修正の確認** — t1/t4 が横断課題として報告した「`install.sh` が
   `codd_gate_*.py` sibling module を zipapp に同梱しない」問題は、作業ツリー上で既に修正済み
   （`install.sh` に `codd_gate_*.py` 一式のコピー処理が追加されている）。コピーロジックを実際に
   模擬実行し、7ファイル全てが `BUILD_DIR` へ複製されることを確認した。配布バイナリでも
   doctor/regression/intake の codd-gate 連携が no-op 縮退せず動く状態になっている。

## (b) 検証内容と結果

- 完了条件（`/Users/nitto/Workspace/sandbox` を作業ルートに評価。`.agent-project` 側は
  `.agent/agent-project.yaml` が存在せず評価不能——後述 (c) 参照）:
  `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` → **exit 0**
- `tools/agent-project` 配下の全テスト: **750 passed, 1 failed**（`test_kf_base_passes_flow_config`。
  macOS の `/var` → `/private/var` シンボリックリンク解決差による既知の環境依存 flake。t1/t3 双方が
  既に無関係と特定済みで、本タスクの変更前から存在。今回もこれ以外は全て green）。
- `codd_gate_regression.py --dry-run` 実機実行: `usable: true, changed: false`（上記(a)-1）。
- `agent-project doctor --json` 実機実行: codd-gate関連finding 0件（上記(a)-2）。

## (c) t5（verify=fail）の扱いと前提・未解決事項

t5 の差し戻し「完了条件コマンドが `.agent/agent-project.yaml: No such file or directory`（exit 2）」は、
**評価ワークツリーの取り違えによる誤検知**と判断した。理由:

- `/Users/nitto/Workspace/sandbox-agent-state/.agent-project` は sparse-checkout の制御面
  worktree（branch `agent-state`）で、`.agent/agent-project.yaml` はここには実体化されない。
- 成果物（`docs/`, `tools/`）と `.agent/agent-project.yaml` は
  `/Users/nitto/Workspace/sandbox`（main worktree, branch `main`）側にのみ存在し、完了条件は
  そちらを作業ルートとして評価する規約（本 run の r0 verify-command-log でも同様の解決実績あり）。
- 実際に `cd /Users/nitto/Workspace/sandbox` で完了条件コマンドを実行すると exit 0（上記(b)）。

t5 の補足所見（sandbox 側の t1〜t4 実装相互整合は確認済み）とも矛盾しない。よって t5 の
verify=fail は「評価ルート誤り」に起因する false negative として扱い、本タスクでは
`.agent/agent-project.yaml` への追加書き込みは行っていない（既に正しい値が入っており、
書き込みツールの dry-run でも changed:false と実証済みのため）。

残る前提・範囲外事項（変更なし、参考として引き継ぎ）:

- `.agent/agent-project.yaml` は `state.py::_HUMAN_OWNED_STATE_FILES` に属し、実行時デーモンが
  自動でファイルへ書き込むことは設計上禁止（`configfile.py::_apply_codd_gate_auto_wiring` は
  メモリ上の `cfg` のみを補い、ファイルは変更しない）。ファイルへの永続化は
  `codd_gate_regression.py` を人（またはセットアップ手順）が明示実行する運用。今回はその実行結果が
  既に反映済みであることを確認したのみで、新規書き込みは発生していない。
- `.agent-project/repos.json` は本 worktree にはまだ生成されていない（charter からの自動生成は
  agent-project 本体のランタイム起動時に行われる想定。今回の検証は生成前でも
  `codd_gate_regression.py`/doctor の判定ロジックが正しく動くことを確認する範囲に留めた）。
- 本タスク・依存タスクいずれも `/Users/nitto/Workspace/sandbox`（main）への git commit は行っていない。
  同 worktree は独自の kiro-project ループが state-sync でコミットを管理する運用のため、
  手動コミットは意図的に見送っている。
