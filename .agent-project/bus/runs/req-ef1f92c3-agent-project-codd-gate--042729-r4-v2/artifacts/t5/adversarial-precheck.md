# t5 verify: 敵対的事前チェック（t2/t3/t4）

## 判定
**verify=fail**

## 独立検算サマリー
- 完了条件コマンドは現 worktree で成立:  
  `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` → exit 0。
- `.agent-project/agent-project.yaml` と `.agent-project/.agent/agent-project.yaml` の `regression_cmd` / `intake_cmd` は一致し、`regression_cmd` 記述は完了条件 grep に合致。
- 検出/取り込み実装の副作用・循環は重大問題なし:
  - `codd_gate_detect.py` / `codd_gate_wiring.py` / `codd_gate_routing.py` は設定ファイル書き換えを行わない。
  - `run_intake` は `verify_timeout` 付き単発実行・exit非0/非JSON時は取り込み中断・既存IDスキップで冪等化。
  - `mr.py` は `task.verify` と `cfg.regression_cmd` を別フェーズで実行し、相互再帰呼び出しはない。

## fail 理由（差し戻し）
1. **t3 の事実誤認（重大）**  
   `artifacts/t3/regression-wiring-verification.md` で「origin/main 側も完了条件に整合」と読める記述があるが、独立確認では不一致。  
   実測: 参照 repo main の `.agent/agent-project.yaml` は  
   `regression_cmd: 'codd-gate verify --debt --sync --repos repos.json --max-broken ...'` であり、`codd-gate verify --base` ではない（grep exit 1）。

2. **t4 の根拠前提が現状態と不整合（重大）**  
   `artifacts/t4/intake-pipeline-live-verification.md` は「`workspace: src` により verify が一時 clone で評価される」ことを主要因としているが、現行 backlog  
   `backlog/agent-project-codd-gate--042729.md` に `workspace` フィールドは存在しない。  
   現在の失敗原因分析としては前提がズレており、統合判断用の証拠として不十分。

## generate ノードへの差し戻し指示
- **t3 へ差し戻し**: 「origin/main 側の設定整合」主張を再検証し、`.agent/agent-project.yaml` の実値・grep 結果を一次証拠付きで更新すること。  
- **t4 へ差し戻し**: `workspace: src` 前提を撤回し、現行 backlog 実体（workspace 無し）で `verify` 失敗理由を再導出すること。過去 needs_reason の履歴と現定義を分離して記述すること。

```json
{"ok": false, "issues": ["t3: origin/main の regression_cmd 整合主張が実測と不一致（.agent/agent-project.yaml は --base ではなく --debt --sync）。", "t4: verify失敗原因の主因を workspace:src とする前提が現 backlog 実体（workspace 無し）と不整合。現状態での再導出が必要。"]}
```
