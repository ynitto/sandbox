# t11-codd-gate-end-to-end-verify

## verify判定

**verify=fail**

## 実行ログ（独立検算）

1. 完了条件コマンド（要求どおり）  
   `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`  
   - exit code: **0**
   - 出力: `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`

2. intake 行の実在確認  
   `grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks --debt' .agent/agent-project.yaml`  
   - exit code: **0**
   - 出力: `intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'`

3. 生成経路（`build_config`）の再現  
   - 実行: `from agent_project import build_config, CONFIG_DEFAULTS` で temp root + `repos.json` を与えて `build_config(args)` を実行
   - 結果(JSON):  
     `{"regression_cmd":"codd-gate verify --base \"$KIRO_BASE_REV\" --repos ./repos.json","intake_cmd":"codd-gate tasks --debt --repos ./repos.json","both_wired":true}`
   - 判定: **生成経路では regression_cmd / intake_cmd の双方が自動配線される**

4. 連携有効性（intake実行系）の再現  
   - 実行: `run_intake(cfg)` に `cfg.intake_cmd`（`python3 -c ...`）を与え、`[title欠落レコード, 非object, 正常1件]` を投入
   - 結果(JSON):  
     `{"created_ids":["ok1"],"created_count":1,"journal_has_record_error":true,"backlog_files":["ok1.md"]}`
   - 判定: **`codd_gate_debt.parse_debt_output` 経由のレコード単位検証が有効**（不正レコードを隔離しつつ正常レコードは取り込み継続）

5. 更新経路（設定ファイル永続化）の再現  
   - 実行: `python3 tools/agent-project/codd_gate_regression.py --config <temp>/.agent/agent-project.yaml --repos <temp>/.agent-project/repos.json`
   - 結果:
     - `HAS_REGRESSION True`
     - `HAS_INTAKE False`
   - 判定: **更新経路の自動更新対象は regression_cmd のみ**（intake_cmd は永続化されない）

6. 関連テストの実行  
   `python3 -m pytest tools/agent-project/tests/test_codd_gate_wiring.py tools/agent-project/tests/test_codd_gate_regression.py tools/agent-project/tests/test_codd_gate_debt.py -q`  
   - 結果: **49 passed**

## 評価（チェック観点）

- (1) 目標・完了条件
  - 完了条件grepは満たす（exit 0）。
  - ただし「設定生成・更新経路から regression_cmd/intake_cmd の双方へ結線」のうち、**更新経路で intake_cmd が欠落**。
- (2) 集計整合
  - 実行テスト件数 49/49 pass（指定3ファイル）。
- (3) 抜け漏れ・重複
  - 生成経路・更新経路・実行経路を分離検証。更新経路の片肺（regression only）を確認。
- (4) 妥当性の抜き取り検査
  - `run_intake` の不正レコード混在ケースで正常継続を再現。
- (5) スコープ外差分混入
  - `git status` 上、主対象（configfile/model/doctor/codd_gate_*・README/設計書・関連tests）が中心。

## issues

1. `tools/agent-project/codd_gate_regression.py` が `.agent/agent-project.yaml` に `regression_cmd` しか永続化せず、**更新経路から `intake_cmd` まで自動結線できていない**。  
   - どこで: `codd_gate_regression.py`（`KEY = "regression_cmd"` 固定、`build_regression_cmd`/`upsert_config_text` の単一キー運用）
   - 何が: 更新経路で `intake_cmd` が未注入（再現結果 `HAS_INTAKE False`）
   - どう直すべきか: `intake_cmd` 用の対称生成関数（例 `build_intake_cmd`）と upsert 経路を追加し、CLI実行1回で `regression_cmd`/`intake_cmd` を冪等更新するか、同等の更新スクリプトを別途実装して「更新経路」要件を満たすこと

