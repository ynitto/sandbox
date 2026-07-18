# configfile を codd_gate 非依存化 — 成果報告

対象: `tools/agent-project/agent_project/configfile.py`（作業ブランチ `ap/agent_project-codd_gate-163827`）

## 成果（何をした / 何が満たされているか）

`build_config` から codd_gate 固有の実行時自動配線を撤去し、configfile を「差し込み点のみ」にした。

- `configfile._apply_codd_gate_auto_wiring(cfg)` 関数と、`build_config` 末尾の同関数呼び出しを削除。
  検証: `import agent_project as ap; hasattr(ap, "_apply_codd_gate_auto_wiring") == False`、
  `hasattr(ap, "build_config") == True`。
- configfile に残る `regression_cmd` / `intake_cmd` は**汎用の設定キー**（CLI > 設定ファイル > 既定の
  素通し）のみ。codd-gate は `CONFIG_DEFAULTS` 内コメントと `--intake-cmd` help の**例示**に現れるだけで、
  検出・probe・sibling import・自動代入といった配線ロジックは configfile に一切残っていない
  （`grep -E "detect_wiring|_codd_gate_wiring_module|resolve_codd_gate|import codd_gate|apply_.*wiring"`
  = 0 hit）。

## 外出し先（externalization — sibling / 設定の明示指定）

自動配線は次の 2 経路へ外出し済み（configfile は関与しない）:

- 検出・提示: `codd_gate_wiring.detect_wiring(...)` + `codd_gate_wiring.doctor_findings(...)`
  — repos.json 実在環境で codd-gate を検出し、未結線なら doctor が推奨コマンド文字列を finding として提示。
- ファイルへの恒久注入: `codd_gate_regression.py`（`main()` の `--config` CLI + `apply_to_file(...)`）
  — 検出結果駆動で `agent-project.yaml` へ冪等注入。
- 明示指定: `regression_cmd` / `intake_cmd` を設定ファイル / CLI に直接書く（build_config はそのまま採用）。

## 検証内容と結果

- `pytest -k "CoddGateNoAutoWiring or ConfigFile"` → **15 passed**
  （repos.json があっても build_config が cmd を補わない・明示値は素通し・関数非存在の回帰ガードを固定）。
- `pytest tests/test_codd_gate_wiring.py tests/test_codd_gate_regression.py` → **39 passed**（外出し先が機能）。
- import sanity: build_config 存在 / `_apply_codd_gate_auto_wiring` 非存在 を確認。
- フルスイート `pytest tests/test_agent_project.py` → **711 passed, 3 failed**。
  失敗 3 件はいずれも本タスク範囲外・本変更と無関係（diff は `TestConfigFile`/`TestCoddGate*` のみ）:
  - `TestJournalRotation::test_rotation_archives_and_starts_fresh`
    — 同一秒タイムスタンプで生成されるアーカイブ名の数値サフィックス（`.1`..`.19`）を文字列ソート
      するため順序が崩れる決定性の問題（環境/タイミング依存）。
  - `TestProjectLayer::test_version_inherits_master_charter` — charter の制約和集合の継承。
  - `TestDaemonRouting::test_kf_base_passes_flow_config` — daemon への flow_config 受け渡し。
  いずれも `regression_cmd`/`intake_cmd`/codd_gate/自動配線に触れず、既存の pre-existing failure。

## 採用した前提 / 未解決事項 / 範囲外の問題

- 前提: 到着時点で作業ツリーに当該変更（configfile 削除・test 更新・README 更新）が**既に適用済み**
  だった（本 run の先行ステップによると解釈）。完了条件と 1 項目ずつ突き合わせて正当性を確認し、
  冗長な再編集はしていない（`tools/agent-project` 配下・許可範囲のみ、追加の書き換えなし）。
- 範囲外で見つけた問題（直さず報告）: 上記フルスイートの pre-existing failure 3 件。特に
  `TestJournalRotation` は同一秒でのアーカイブ名サフィックスの文字列ソート起因で決定性が崩れており、
  ゼロ埋め or 生成順ソートで安定化する余地がある（@followup: journal ローテーションのアーカイブ名を
  ゼロ埋め連番にして lexicographic ソートを安定化する）。
