verify=fail

重大な差し戻し事項:

1. **完了条件コマンドがこのタスク実行ワークツリーで満たせていない**
   - どこで: `/Users/nitto/Workspace/sandbox-agent-state/.agent-project`
   - 何が: 指定コマンド  
     `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`  
     が `grep: .agent/agent-project.yaml: No such file or directory`（exit 2）
   - どう直すべきか:
     - 完了条件を評価する作業ルートを `sandbox` 側に固定する（例: `cd /Users/nitto/Workspace/sandbox && ...`）か、
     - このワークツリー側にも `.agent/agent-project.yaml` を用意して同一条件で評価できるようにする。

補足（minor）:
- `sandbox` 側の実装整合は確認済み（検出ロジック `codd_gate_wiring.py`、regression 注入 `codd_gate_regression.py`、intake 経路 `model.py` + `codd_gate_debt.py`、設計/README 更新、関連テスト群 pass）。
- ただし本タスクの「完了条件」判定は上記の通り現ワークツリー基準で不成立。

{"ok": false, "issues": ["完了条件コマンド `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` が現ワークツリー `/Users/nitto/Workspace/sandbox-agent-state/.agent-project` で exit 2（.agent/agent-project.yaml 不在）。評価ルートを sandbox 側へ固定するか、当該ファイルをこのワークツリーに配置して再判定が必要。", "(minor) sandbox 側の t1〜t4 実装相互整合（検出/regression/intake/docs とデータ契約）は確認できた。"]}
