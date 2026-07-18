# verify レポート（独立再検証） — agent_project-codd_gate-163827

判定: **pass**

対象コミット: `65d32ee1f14ff43a680d6b6ea0038d12f60a2f3b`（ブランチ `ap/agent_project-codd_gate-163827`、作業ツリー clean）
比較ベース: **merge-base `9a7302ffc170b047c504d0f9db0ec5581ccb3354`**（別 worktree を provision して実行）

> 同ディレクトリに先行の `verify-report.md` が存在した（自分の出力ではない）。判定は一致するため
> 上書きせず本ファイルを追加した。差分は「ベースの取り方」（先行=main 先端 550c2bb / 本レポート=
> merge-base 9a7302f）と、後述の issue 2（README の恒久注入経路）。

## 1. 完了条件コマンド（backlog の verify 行をそのまま実行）

```
PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py \
  TestIntake.test_run_intake_enqueues_and_dedups_by_id \
  TestLoopEngineering.test_regression_gate_blocks_on_failure \
  TestLoopEngineering.test_regression_gate_passes \
&& ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
     -- tools/agent-project/agent_project
```

`Ran 3 tests ... OK` → grep 0 hit → **最終 exit code 0**。差し戻し不要。

## 2. 独立再導出

| 観点 | 実施内容 | 結果 |
|---|---|---|
| 回帰 | merge-base `9a7302f` を別 worktree に取得しフルスイート実行、HEAD と突き合わせ | ベース **716 tests / failures=3**、HEAD **714 tests / failures=3**。失敗テスト名は完全一致（`TestDaemonRouting.test_kf_base_passes_flow_config` / `TestJournalRotation.test_rotation_archives_and_starts_fresh` / `TestProjectLayer.test_version_inherits_master_charter`）→ pre-existing 確定、新規 fail ゼロ |
| 件数整合 | `TestCoddGateAutoWiring` 6 件削除 → `TestCoddGateNoAutoWiring` 4 件追加 | 716 − 6 + 4 = 714。実測一致（テストを削って受入を通した形跡なし） |
| 残存参照 | 除去シンボル 5 種をリポジトリ横断 grep | `agent_project/` 配下 0 hit。唯一のヒットは `test_agent_project.py:3893` の `assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))`（再導入ガード。stale ではない）。ダングリング呼び出しなし |
| 代替経路の実挙動 | `repos.json` のみ置いた一時ディレクトリで `agent-project.py doctor --json` を実行 | 「codd-gate は検出済みだが regression_cmd が未結線」「同 intake_cmd」の 2 finding が、完全なコマンド文字列を `fix:` に含めて出力。自動配線除去後も結線導線が実環境で生きていることをテスト経由でなく CLI 出力で確認 |
| 周辺スイート | 境界周辺 4 クラス 33 tests / sibling `test_codd_gate_*.py` 5 スイート 81 tests | すべて OK |
| スコープ | `git diff --name-only 9a7302f..HEAD` | 7 ファイル全て `tools/agent-project/` 配下。範囲外の差分ゼロ |
| 集計の照合 | synth 報告「796 passed」 | 714（本体）+ 85（sibling 群、charter_version_overrides 4 含む）= 799 収集 − 3 失敗 = 796。**矛盾なし** |

## 3. issues（すべて minor。fail 理由にしない）

1. **(minor / 書込許可範囲外)** `docs/designs/codd-gate-design.md` L271・L277 が `codd_gate_debt.parse_debt_output` を intake 側の本線パーサとして、L286 が `_apply_codd_gate_auto_wiring` を現存機能として記述しており陳腐化している。README がこの §4.1 を参照しているため追随が要る。本タスクの書込許可（`tools/agent-project` 配下のみ）外なので未修正で正しい。
2. **(minor)** `tools/agent-project/README.md` L279-282「上記2行 … ファイルへ恒久的に書き込むには `python3 codd_gate_regression.py --config .agent/agent-project.yaml` を実行する（検出結果駆動で冪等注入）」は 2 行のうち 1 行しかカバーしない。`codd_gate_regression.py:44` は `KEY = "regression_cmd"` の 1 キーのみを upsert し、同ファイル L23 が `intake_cmd` の注入を「別タスクの担当」と明記している。自動配線の除去でファイル注入が唯一の恒久経路になった分、この記述ズレの実害が増した。README 該当段落を「regression_cmd のみ注入。intake_cmd は手書き（または doctor の finding をコピー）」と限定するか、`codd_gate_regression.py` に `intake_cmd` の upsert を追加する。
3. **(minor)** `agent_project/doctor.py:296` の `provider = "codd_gate_wiring"` が、受入 grep には掛からないもののパッケージ内に残る唯一の codd_gate 固有名。プロバイダ名を設定キー（例 `wiring_provider`、既定 `"codd_gate_wiring"`）へ逃がせばコードでなくデータになり、hints の「パッケージ内に codd_gate 名を残さない」をより満たす。
4. **(minor)** `doctor.doctor_wiring_findings` / `_wiring_module` を直接叩くテストが 0 件。merge-base 時点の旧名 `doctor_codd_gate_findings` も 0 件（`git grep` で確認）なので本変更による退行ではないが、doctor が唯一の結線導線になった以上、最低限「`_wiring_module` が None → 空リストへ縮退」と「repos.json あり → 未結線 finding が出る」の 2 ケースは固定したい。今回の改名はテストでは検出できず、上表の CLI 実行で担保した。
5. **(minor)** `codd_gate_debt.parse_debt_output` は `agent_project/` から参照されなくなり、実利用者は自身のテスト `tests/test_codd_gate_debt.py` のみ。docstring に「独立したアダプタとして残る」と明示されており意図的な残置。現状維持でよいが、次に触る際は実利用者の有無を再確認する。

@followup `docs/designs/codd-gate-design.md` §4.1「自動検出レイヤ」と L271/L277 のモジュール表を、configfile 自動配線の除去・intake 検証の `_parse_intake_records` 移管に合わせて更新する（`docs/` への書込許可が必要）
@followup `tools/agent-project/README.md` の恒久注入の記述を `regression_cmd` 限定に直す、または `codd_gate_regression.py` に `intake_cmd` の upsert を追加する
@followup `doctor_wiring_findings` の縮退ケース／finding 生成ケースのユニットテストを `tests/test_agent_project.py` に追加する
