# test_codd_gate_*.py の新境界追随（t6）

対象: `tools/agent-project/tests/test_codd_gate_{regression,wiring,routing}.py`
検証コマンド: `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py'`
結果: **97 tests OK**（着手前 81 → 完了時 97）

## 新境界に対応するテストの所在（後続・検証役向けの対応表）

| 完了条件の観点 | テスト | ファイル:クラス |
|---|---|---|
| CLI（`--config`）経由の検出→生成→注入の一気通貫 | `test_detects_generates_and_injects_in_one_pass` | test_codd_gate_regression.py:TestCliMain |
| 明示設定（`--repos` / `--base`）が推定に勝つ | `test_explicit_repos_and_base_flags_override_inference` | 同上 |
| 明示設定（`--codd-gate` / `explicit=`）が PATH 解決に勝つ | `test_explicit_binary_bypasses_path_lookup` | test_codd_gate_wiring.py:TestDetectWiringIntegrated |
| 明示設定（`hooks:`）による本体→sibling の解決 | `test_explicit_hooks_setting_selects_this_module` ほか計7件 | test_codd_gate_wiring.py:TestHookResolution |
| 推奨文字列の生成 | `TestRecommendedCommands` / `TestJudgeWiringPure` / `TestDoctorFindings` | test_codd_gate_wiring.py |
| **yaml 冪等注入・二回実行で差分ゼロ（挿入経路）** | `test_second_run_produces_no_diff` | test_codd_gate_regression.py:TestCliMain |
| **同（更新経路＝既存値の置換）** | `test_stale_value_is_updated_in_place_then_stable` | 同上 |
| 同（手書き正準値に対しては初回から no-op） | `test_hand_written_canonical_config_is_left_untouched` | 同上 |
| 未検出時の no-op 縮退（人の手書き設定を壊さない） | `test_noop_when_codd_gate_not_detected` | 同上 |

冪等性は3経路すべてで押さえた: **挿入**（キー不在→新規行）・**更新**（既存値と異なる→置換）・
**初回 no-op**（既に正準値と一致）。いずれも「2回目は `changed=False`、本文一致、mtime 不変」を主張する。

## 「自動配線前提」の残存

着手時点で `test_codd_gate_*.py` に自動配線（`build_config` がメモリ上で cfg を書き換える）を
前提にしたテストは**残っていなかった**（`grep -n "自動配線|auto_wiring|build_config|configfile"` の
ヒットは、いずれも「自動配線は存在しない」と明記する docstring 3件のみ）。
差し替えではなく、新境界の各経路を**明示的に固定するテストの追加**が実質の作業になった。

なお `tests/test_agent_project.py:3998-` の `TestCoddGateNoAutoWiring`（再導入を禁じる回帰ガード）は
本タスクのファイルパターン外かつ t1 が「意図的に残す」と判定済みのため、触っていない。

## 変異検査（テストが実効性を持つことの確認）

worktree ではなく一時コピーに対して production 側を壊し、対応するテストが落ちることを確認した:

| 変異 | 落ちたテスト |
|---|---|
| `upsert_config_text` の「同値なら無変更」分岐を削除（冪等性の破壊） | 6件（新規3件を含む） |
| `main()` が `--repos` 明示指定を無視して推定へ倒す | 1件（`test_explicit_repos_and_base_flags_override_inference`） |
| `resolve_codd_gate` が `explicit` を無視して PATH 解決へ倒す | 1件（`test_explicit_binary_bypasses_path_lookup`） |

## 前提・申し送り

- **採用した前提**: CLI テストは `--codd-gate /opt/bin/codd-gate`（実在しないパスの明示指定）で
  検出を決定的にした。`detect_status` は実在確認のみで subprocess を起動しないため、これで
  「その機械に codd-gate が入っているか」にテストの成否が左右されなくなる。
- **採用した前提**: 「二回実行で差分が出ない」を、内容一致に加え **mtime 不変**まで含めて解釈した
  （`apply_to_file` が無変更時に書き込み自体を省略する設計に合わせた）。
- 着手時点で worktree に t6 相当の未コミット変更（TestCliMain / TestHookResolution の初版、
  routing の docstring 修正）が既に存在していた。取り消さずその上に積んだ。
- @followup（範囲外・未着手）: `codd_gate_base.py` は呼び出し元も `tests/test_codd_gate_base.py` も
  無いまま（t1 の申し送り⑤と同じ）。存置/削除が決まるまでテストは書いていない。
- @followup（範囲外・未着手）: `test_codd_gate_wiring.py` の `_load_hooks_fragment()` は
  `agent_project/hooks.py` を自前で exec して読む。`hooks.py` が新たな import を必要とするよう
  変わると、この helper の globals 追加が要る（結合の弱い箇所として記録）。
