# intake 汎用フック維持 — 検証レポート

対象: `tools/agent-project/agent_project/model.py` の `run_intake` / `_parse_intake_records`
ブランチ: ap/agent_project-codd_gate-163827
結論: **コード変更なし（既に汎用フックの形）。回帰テスト維持を確認。**

## 完了条件と突き合わせ

1. schemas/task 相当の汎用 JSON パース — 満たす
   - `_parse_intake_records` は json.loads した結果を「トップレベル object（1件）/ array（複数件）」の
     両対応で正規化。レコード単位に `dict であること`＋`title 非空`のみ検証し、未知キーは保持したまま通す。
   - これは `schemas/task.schema.json`（`required: ["title"]` / `additionalProperties: true` / `id` は任意）
     の契約と一致（実物を parse して確認）。特定検出器のスキーマには依存しない。
   - 1件の不備（非 object・title 欠落）は該当レコードだけ errors に落とし、残りは取り込む。

2. id 冪等 — 満たす
   - `run_intake` は現役 backlog（`cfg.backlog.glob("*.md")` の stem 集合）に spec の `id`(_slug_id 正規化)
     が居れば飛ばす。同一 run 内で追加した id も `existing` に足して二重取り込みを防ぐ。

3. codd_gate 非依存 — 満たす
   - intake 消費側（model.py）に `codd_gate*` のコード import / 呼び出しは無い（grep 済み）。
   - 残る "codd-gate" 参照は docstring 内の**例示のみ**（「codd-gate 等」）で、むしろ
     『本体は無改造・差し込み点のみ／どの検出器かを問わない』設計を明文化している。
   - JSON を生む側（codd_gate_debt / codd-gate CLI）と consume 側（intake）は分離済み。
     intake は `codd_gate_debt.parse_debt_output` 等を一切呼ばない。

## 検証内容と結果

- 対象回帰 `TestIntake.test_run_intake_enqueues_and_dedups_by_id`: **PASS**
- `TestIntake` 全10件（interval throttle / failure tolerance / one-bad-record / loop intake / idle wake 含む）: **10 passed**
- intake 依存経路（Intake/Enqueue/Spec/ingest 系）: **61 passed, 0 failed**
- パッケージ合成（`agent_project/__init__.py` の exec 合成）健全性: **OK**（run_intake / _parse_intake_records callable）

実行環境: Python 3.9.6 / pytest 8.4.2 / cwd = tools/agent-project

## 採用した前提

- 本タスクは「維持＋回帰確認」であり、intake は既に完了条件を満たすためコード変更を行わない
  （最小変更の原則）。docstring の "codd-gate" 例示は設計意図の明文化であり、受入の
  codd_gate **識別子**除去条件（対象は `configfile._apply_codd_gate_auto_wiring` /
  `doctor._codd_gate_wiring_module` / `doctor_codd_gate_findings` 等の**コードシンボル**）とは別物。
  model.py の intake 経路に `codd_gate`（アンダースコア識別子）は存在しない。

## 範囲外（手を出さず報告のみ）

- configfile.py / doctor.py の codd_gate 配線除去は**別タスクの担当**（本タスクは触れていない）。
  agent_project は単一名前空間へ exec 合成するため、それら sibling 変更が壊れると intake テストも
  巻き込まれてロードに失敗し得る。統合後は本レポートのコマンドで再確認を推奨:
  `python3 -m pytest tests/test_agent_project.py -k TestIntake -q`
