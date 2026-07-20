## sibling-163827: sibling 自動検出レイヤと利用手順を新境界へ追随させる
- status: done
- source: charter
- priority: 0
- verify: `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py' && grep -nE 'codd_gate_regression|regression_cmd|intake_cmd' tools/agent-project/README.md && ! grep -nE 'build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring' tools/agent-project/README.md`
- retries: 4
- workspace: agent-project
- why: パッケージ外の検出・yaml 注入・doctor 所見の置き場と README の有効化手順を一貫させ、利用者が結線方法を迷わないようにするため。
- out_of_scope: agent_project パッケージ内への再結合・dashboard 変更
- hints: tools/agent-project/codd_gate_{detect,status,routing,base,debt,wiring,regression}.py は残し、責務を『検出・推奨文字列・yaml 冪等注入・（必要なら）CLI 所見』に限定。README / GUIDE から build_config メモリ自動配線の記述を消し、明示設定または `python3 codd_gate_regression.py --config …` へ誘導。docs/designs/codd-gate-design.md §4.1 の『現在地』も実装に合わせて更新（文章は slop-police）。
- charter: v1
- after: agent_project-codd_gate-163827
- assess: c=2 r=2 a=2
- needs_reason: 繰り返し NG（retries=4）: agent-flow run タイムアウト（3600s）
- last_run: req-ef1f92c3-sibling-163827-r0
- archived: 2026-07-20 11:49:08

## 納品書
- 完了 : 2026-07-20 11:49:08
- verify: `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py' && grep -nE 'codd_gate_regression|regression_cmd|intake_cmd' tools/agent-project/README.md && ! grep -nE 'build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring' tools/agent-project/README.md` → PASS（exit=0 codd-gate を検出できたのに `regression_cmd`/`intake_cmd` が 447:- **取り込みコマンド（intake_cmd）**: 外部の決定的ゲート/検出器を **watch の周期で pull** する汎用フック（push 型の 448:  inbox と対）。設定 `intake_cmd:`（CLI `--intake-cmd`）のコマンドをパ）
- 成果 : commit ef1f92c3

## 判断材料（成果物の所在・差分・検証）
- 成果物: commit ef1f92c3
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
