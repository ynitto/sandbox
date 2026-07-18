# model.py: codd_gate 依存の除去（差し込み点のみへ）

## 成果
`tools/agent-project/agent_project/model.py` から codd_gate 実装への直接依存を除去した。

- 削除: `_codd_gate_debt_module()`（`import codd_gate_debt` を sys.path 経由で遅延 import していた関数）。
- 追加: `_parse_intake_records(text) -> (specs, errors)` — intake_cmd の stdout（`enqueue --json` と
  同形式＝1件の object か配列）をレコード単位に検証する**検出器非依存の汎用パーサ**。
- `run_intake` を、`_codd_gate_debt_module()` 分岐から `_parse_intake_records` 一本へ簡素化。

設計上の位置づけ（採用した前提）: README §「外部 CLI の差し込み点」が明示する通り、**差し込み点は
`intake_cmd` 設定そのもの**であり、codd-gate はそこへ挿す一例にすぎない。よってレコード検証は特定
検出器に紐づく振る舞いではなく intake フックの汎用契約なので、model 本体にネイティブ実装として持たせ、
`import codd_gate_*` を断った。model は「本体は無改造・差し込み点（intake_cmd）のみ」を満たす。

## 挙動の同一性（重要）
テスト環境では従来から `codd_gate_debt` が sibling として import 可能で、実効経路は「レコード単位検証」
だった。`_parse_intake_records` はその経路と等価:
- 空/空白 → 0 件（正常系）
- 非 JSON → `JSON として解釈できない: …` を errors に1件（specs 空）
- 非 object レコード → `[i] レコードが object ではない（…）`
- title 空/欠落 → `[i] title が空/欠落している（task.schema.json の required を満たさない）`
- errors は従来どおり `intake レコード無効: {err}` として journal へ流す。

## 検証
- `python3 -m unittest tests.test_agent_project.TestIntake tests.test_codd_gate_debt` → 16 tests OK。
  - `test_run_intake_one_bad_record_does_not_block_the_rest`（"title が空/欠落" を journal に要求）含め通過。
- パッケージ exec 合成 OK（`_parse_intake_records` あり／`_codd_gate_debt_module` なしを確認）。
- 全スイート `unittest discover`（801 tests）: 失敗は 3 件のみで、いずれも **本変更と無関係の既存/環境依存**:
  1. `TestProjectLayer.test_version_inherits_master_charter`（charter 制約の和集合）
  2. `TestJournalRotation.test_rotation_archives_and_starts_fresh`（journal ローテーションの順序）
  3. agent-flow.yaml パス比較（macOS `/var`→`/private/var` シンボリックリンク）
  1・2 は HEAD の未改変 model.py に一時退避して同テストを流し、**同一に失敗**することを確認済み（＝先行既存）。

## grep 受入
`grep -n "codd_gate\|import codd" model.py` → 該当なし。model は codd_gate 実装へ非依存。
（package 全体の残存 `import codd_gate_wiring` は configfile.py / doctor.py。別タスクの担当で本タスク対象外。）

## 範囲外で見つけた問題（手を出していない・別タスク判断）
- `tools/agent-project/codd_gate_debt.py` の module docstring（25–26 行）が
  「model.py の run_intake/_codd_gate_debt_module が本 module を遅延 import する」と記述しており、
  本変更で **stale** になった。codd_gate_debt.py は sibling adapter で本タスク（model 限定）の対象外、
  かつ他タスクが再編する可能性があるため未編集。docstring の追随更新を推奨（@followup）。
- @followup codd_gate_debt.py の docstring から model 遅延 import の記述を更新（stale 参照の解消）
