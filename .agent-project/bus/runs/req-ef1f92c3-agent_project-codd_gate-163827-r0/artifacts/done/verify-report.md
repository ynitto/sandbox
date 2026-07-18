# verify レポート — agent_project-codd_gate-163827

判定: **pass**

対象コミット: `65d32ee1f14ff43a680d6b6ea0038d12f60a2f3b`（ブランチ `ap/agent_project-codd_gate-163827`）
検証 cwd: `/var/folders/8c/.../agent-flow-ws-6682-6fld_v1i/sandbox`（作業ツリー、clean）

## 1. 受入コマンド（backlog の verify 行そのまま）

```
PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py \
  TestIntake.test_run_intake_enqueues_and_dedups_by_id \
  TestLoopEngineering.test_regression_gate_blocks_on_failure \
  TestLoopEngineering.test_regression_gate_passes \
&& ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
     -- tools/agent-project/agent_project
```

`Ran 3 tests / OK`、grep 0 hit、**終了コード 0**。

## 2. 回帰の有無（main ベースラインとの突き合わせ）

別 worktree に `main`（550c2bb）を取得し、同一手順でフルスイートを実行して比較した。

| | 実行数 | 失敗 |
|---|---|---|
| main (550c2bb) | 721 | 3 |
| HEAD (65d32ee) | 714 | 3 |

失敗 3 件は両者で同一（`TestJournalRotation.test_rotation_archives_and_starts_fresh` /
`TestProjectLayer.test_version_inherits_master_charter` / agent-flow.yaml の `/private` 実パス差分）。
**pre-existing であることを実測で確認**したので、本変更による回帰はない。

実行数の差 −7 の内訳も突き合わせた:

- −2: 本タスクによる `TestCoddGateAutoWiring`(6件) → `TestCoddGateNoAutoWiring`(4件) の書き換え
- −5: `main` が分岐点（merge-base `9a7302f`）以降に追加したテスト
  （`test_approve_without_mr_fast_forwards_target_branch` ほか 4 件）。分岐点の
  `test_agent_project.py` には存在しないことを `git show` で確認済み。本タスクの削除ではない。

sibling スイートも全通過: wiring 19 / debt 10 / regression 20 / detect 23 / routing 9 /
charter_version_overrides 4 = **85 passed**。
（synth 報告の「796 passed」は 714 + 85 = 799 収集 − 3 失敗と一致する。集計に矛盾なし）

## 3. 実挙動の確認（テストではなく CLI を叩いた）

`_apply_codd_gate_auto_wiring` の除去は「起動時に cfg を自動で埋める」機能の削除にあたるため、
代替経路（doctor が推奨コマンドを提示する）が実際に動くかを一時プロジェクトで確認した。

```
$ D=$(mktemp -d); echo '{"app":{"url":"git@h:t/a.git"}}' > $D/repos.json
$ agent-project.py doctor --root $D --json
  … "title": "codd-gate は検出済みだが regression_cmd が未結線",
    "fix": "agent-project.yaml に設定: regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\" --repos …/repos.json'"
```

`importlib` 越しの sibling 解決（`doctor._wiring_module`）が実環境で成立し、README が書く
「生成・結線は sibling へ外出し、doctor が finding として提示」が実挙動と一致することを確認した。

## 4. intake パーサ差し替えの意味的同値性

`codd_gate_debt.DriftItem.to_spec()` 経由 → `model._parse_intake_records` の素通しへ変わったため、
正規化の欠落がないかを追った。

- 旧 `to_spec()` は `title`/`id` を strip し、空 id を落としていた
- 新パーサは raw dict を素通しするが、下流の `run_intake` が `_slug_id(sp["id"])` で、
  `task_from_spec` が `title.strip()` / `_gen_task_id(_slug_id(explicit))` で同じ正規化を行う

→ 空白付き id・空 id・空白付き title のいずれも下流で同一結果に落ちる。**振る舞いは同値**。

## 5. スコープ

`git diff --name-only main...HEAD` は 7 ファイル、すべて `tools/agent-project/` 配下。範囲外の
差分はない。sibling 2 ファイル（`codd_gate_debt.py` / `codd_gate_wiring.py`）の変更は docstring
のみだが、いずれも「もう存在しない依存関係」を現存として書いていた記述の訂正であり、
out_of_scope の「設計書の文章だけの推敲」には当たらないと判断した。

`git merge-tree --write-tree main HEAD` はコンフリクトなし（5 コミット遅れているが auto-merge 可）。

## 6. issues（すべて minor。fail 理由にはしない）

1. **(minor)** `agent_project/doctor.py:296` の `provider = "codd_gate_wiring"` は、受入 grep には
   掛からないがパッケージ内に残る唯一の codd_gate 固有名（hints の「パッケージ内に codd_gate 名を
   残さない」に対する未達分）。プロバイダ名を設定キー（例 `wiring_provider`、既定
   `"codd_gate_wiring"`）へ逃がせば、コードではなくデータになり意図をより満たす。
2. **(minor)** `doctor_wiring_findings` / `_wiring_module` を直接叩くテストが 0 件
   （`main` 側の旧名 `doctor_codd_gate_findings` も同様に 0 件＝pre-existing）。今回の改名は
   テストでは検出できず、本レポートは §3 の手動 CLI 実行で担保した。`tests/test_agent_project.py`
   に「repos.json あり＋sibling 解決可 → regression 未結線 finding が 1 件出る」テストを追加すると
   同種の改名を機械的に守れる。
3. **(minor / 書込許可範囲外)** `docs/designs/codd-gate-design.md` の L271・L277（`codd_gate_debt`
   を intake 側パーサとして記載）と L286（`_apply_codd_gate_auto_wiring` を現存として記載）が
   陳腐化している。README がこの §4.1 を参照しているため、別タスクで追随が要る。
