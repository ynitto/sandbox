# t11-codd-gate-end-to-end-verify

## verify判定

**verify=fail**

## 実行ログ（独立検算）

1. 完了条件コマンド（要求どおり）  
   `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`  
   - exit code: **0**  
   - 出力: `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`

2. intake 行の実在確認  
   `grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks --debt' .agent/agent-project.yaml`  
   - exit code: **0**  
   - 出力: `intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'`

3. 設定生成経路（`build_config`）の再現  
   - 検証コード: `agent_project.build_config(args)` に `root/repos.json`（schema互換）を用意した temp root を渡して実行  
   - 結果:  
     `{"generation_regression_cmd":"codd-gate verify --base \"$KIRO_BASE_REV\" --repos ./repos.json","generation_intake_cmd":"codd-gate tasks --debt --repos ./repos.json","generation_both_wired":true}`  
   - 判定: **生成経路では regression_cmd/intake_cmd の双方が自動検出で結線される**

4. 更新経路（設定ファイル永続化）の再現  
   - 実行:  
     `python3 tools/agent-project/codd_gate_regression.py --config <temp>/.agent/agent-project.yaml --repos <temp>/.agent-project/repos.json`  
   - 結果:  
     `{"has_regression_cmd": true, "has_intake_cmd": false}`  
   - 判定: **更新経路は regression_cmd のみ更新し、intake_cmd は更新しない**

5. 実行経路（連携有効性）の再現  
   - 検証コード: `run_intake(cfg)` に `cfg.intake_cmd` で `[{正常spec}, {title欠落spec}, 非object]` を返すコマンドを与えて実行  
   - 結果:  
     `{"created_ids":["ok1"],"journal_has_invalid_record":true}`  
   - 判定: **`codd_gate_debt.parse_debt_output` によるレコード単位検証が有効（不正レコード隔離 + 正常レコード継続）**

6. 関連テスト  
   `python3 -m pytest tools/agent-project/tests/test_codd_gate_wiring.py tools/agent-project/tests/test_codd_gate_regression.py tools/agent-project/tests/test_codd_gate_debt.py -q`  
   - 結果: **49 passed**

## 評価（チェック観点）

- (1) 目標・完了条件: 完了条件grepは充足（exit 0）。ただし「設定生成・更新経路から regression_cmd/intake_cmd の双方へ結線」は**更新経路で未充足**。
- (2) 集計整合: 指定3テストファイル合計 49/49 pass。
- (3) 抜け漏れ・重複: 生成・更新・実行の3経路を分離検証し、欠落点は更新経路の intake_cmd に限定。
- (4) 妥当性抜き取り: run_intake の不正混在入力で正常継続を実測。
- (5) スコープ外差分: `git diff --name-only` の先頭確認では codd-gate/agent-project周辺と設計書が中心で、本件と無関係な混入は目立たない。

## issues

1. `tools/agent-project/codd_gate_regression.py` が `regression_cmd` 単独運用で、更新経路から `intake_cmd` を自動結線できない。  
   - どこで: `codd_gate_regression.py`（`KEY = "regression_cmd"` 固定、`build_regression_cmd`/`upsert_config_text` の単一キー更新）  
   - 何が: 更新経路再現で `has_regression_cmd=true` かつ `has_intake_cmd=false`  
   - どう直すべきか: `intake_cmd` 用の対称生成（例: `build_intake_cmd`）と upsert を追加し、同一更新処理で `regression_cmd` と `intake_cmd` を冪等更新できるようにする
