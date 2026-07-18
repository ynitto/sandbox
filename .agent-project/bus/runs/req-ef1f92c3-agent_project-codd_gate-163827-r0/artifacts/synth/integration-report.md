# synth: 4モジュール統合と設計書の新境界整合

対象ブランチ: `ap/agent_project-codd_gate-163827`（HEAD `b694cb9`）
統合入力: `cfg`（configfile）/ `doc`（doctor）/ `mdl`（model）/ `itk`（intake 検証）/ `test`（テスト追随）

## 1. 統合後の境界（確定形）

`agent_project/` パッケージ本体には codd_gate 実装への依存が一切残っていない。差し込み点は
**設定キーそのもの**（`regression_cmd` / `intake_cmd`）で、codd-gate 固有の判断は全て sibling
（`tools/agent-project/` 直下、パッケージ外）へ外出しした。

| 層 | 旧 | 新 | 担当 |
|---|---|---|---|
| Config 生成 | `configfile._apply_codd_gate_auto_wiring` が実行時に自動配線 | 関数ごと除去。CLI > 設定 > 既定の素通しのみ | `cfg` |
| 配線の検出・提示 | configfile に埋め込み | `doctor._wiring_module()` → sibling `codd_gate_wiring` を `importlib` 遅延解決 → `doctor_wiring_findings` | `doc` |
| 配線の永続注入 | （なし） | `codd_gate_regression.py --config`（冪等注入・sibling 側 CLI） | 既存 |
| intake 検証 | `model._codd_gate_debt_module()` → `codd_gate_debt.parse_debt_output` | `model._parse_intake_records`（検出器非依存の汎用パーサ・本体同梱） | `mdl` |

この結果、パッケージ内に残る sibling ローダは `doctor._wiring_module()` の **1 箇所のみ**。

## 2. 依存タスク報告の矛盾・陳腐化（解消済み・要記録）

統合にあたり入力を突き合わせた結果、次の 3 点が実体と食い違っていた。

1. **`doc` の引き継ぎ「`configfile.py:220` が旧名 `_codd_gate_wiring_module()` を呼ぶ」は解消済み。**
   `doc` は改名時点の観測を書いたが、後続の `cfg`（`b694cb9`）が呼び出し側の関数を丸ごと除去したため、
   改名追随ではなく参照自体が消滅した。現在 `configfile.py` に配線コードは 0 行。

2. **`doc` / `test` が挙げた @followup「doctor `_wiring_module` と model 側 debt ローダを
   `_sibling_module(name)` へ DRY 統合」は moot（対象消滅）。**
   `mdl` が `_codd_gate_debt_module` を削除済みで、同型のローダは既に doctor 側 1 箇所しか無い。
   `doc` は `mdl` 着地前に書き、`test` はそれを再検査せず引き写していた。統合対象が無いため実施しない。

3. **`mdl` が @followup に回した `codd_gate_debt.py` docstring の stale 化は、本タスクで修正した。**
   `mdl` は「model 限定タスクなので対象外」として残したが、これはモジュール**境界の記述**であり、
   本タスクの「設計書を新境界に合わせて整合更新」に該当するため synth で処理した（下記 §3）。

## 3. 本タスクの変更（設計書＝境界記述の整合更新のみ。文章推敲はしていない）

除去された `model → codd_gate_debt` 遅延 import 関係を、まだ現存すると記述していた 3 箇所を修正。

| ファイル | 旧記述 | 修正後 |
|---|---|---|
| `codd_gate_debt.py`（module docstring） | 「`run_intake`/`_codd_gate_debt_module` が本 module を遅延 import し、使えれば `parse_debt_output`、使えなければ緩いパースへ no-op 縮退」 | 「本体の intake 経路は本 module に依存しない。差し込み点は `intake_cmd` 自体で、本体は `_parse_intake_records` で同じレコード単位検証を行う。本 module は `DriftItem` 正規化が必要な呼び出し側のための独立アダプタ」 |
| `codd_gate_wiring.py`（非責務リスト） | 「enqueue 経路統合は `codd_gate_debt.py`/`model.py` の `run_intake` が既に担う」 | 「`model.py` の `run_intake` が検出器非依存の `_parse_intake_records` で担う。本体は差し込み点のみ」 |
| `tests/test_agent_project.py:290`（コメント） | 「codd_gate_debt.parse_debt_output 経由のレコード単位検証」 | 「model 本体同梱の `_parse_intake_records` によるレコード単位検証。検出器非依存」 |

`README.md` は `cfg` が §「一貫性ゲート」を新境界へ更新済み（差し込み点のみ／sibling 外出し／
未検出時 no-op 縮退）で、追加の不整合は無し。`GUIDE.md` / `ROUTING.md` は codd 参照ゼロ。

## 4. 検証（cwd = `tools/agent-project`, Python 3.9.6 / pytest 8.4.2）

- 受入 grep — `agent_project/` に `import codd_gate` / `_apply_codd_gate` / `_codd_gate` /
  `doctor_codd_gate_findings` → **0 hit**。
- 除去済みシンボルの残存参照（`tools/agent-project` 全体）→ `tests/test_agent_project.py:3893` の
  `assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))` **のみ**（意図的な非存在ガードで stale ではない）。
- 新境界シンボル実在 — `model._parse_intake_records` / `doctor._wiring_module` / `doctor.doctor_wiring_findings`。
- sibling 4 スイート（debt / wiring / regression / detect）→ **72 passed**。
- `-k "Intake or CoddGate or LoopEngineering or Doctor or ConfigFile"` → **54 passed**。
- フルスイート `pytest tests/` → **796 passed, 3 failed**（本タスクの編集前後で同一）。

失敗 3 件は 4 タスク全報告と一致する pre-existing failure で、本境界とは無関係:
`TestDaemonRouting::test_kf_base_passes_flow_config` /
`TestProjectLayer::test_version_inherits_master_charter` /
`TestJournalRotation::test_rotation_archives_and_starts_fresh`。
最後の 1 件は実行ログ上でアーカイブ名が `.1, .10, .11 … .19, .2` の順に並んでおり、
`cfg`/`test` が推定した「数値サフィックスの文字列ソート起因の決定性欠如」が実出力で裏付けられた。

## 5. followup（スコープ外・未着手）

- `@followup:` `TestJournalRotation` のアーカイブ名をゼロ埋め連番（`.01`..`.19`）にして
  lexicographic ソートを安定化する。上記 §4 の実出力が原因を確定させている。
- `@followup:` `TestProjectLayer::test_version_inherits_master_charter` /
  `TestDaemonRouting::test_kf_base_passes_flow_config` の pre-existing failure 調査。
- `@followup:` `docs/designs/codd-gate-design.md` §4.1「自動検出レイヤ」は README から参照されているが、
  本 run で configfile の実行時自動配線が消えたため記述が実体とズレている可能性が高い。
  ワークスペースの書込許可範囲（`tools/agent-project` 配下）外のため未確認・未編集。
- `@followup:` `codd_gate_debt.py` は本 run 以降 agent-project 本体からの利用者が無くなり、
  参照元は自身の単体テストのみ。standalone アダプタとして残す（今回の判断）か整理するかは要判断。
- `doc`/`test` が挙げた `_sibling_module(name)` への DRY 統合は**実施しない**（§2-2 のとおり対象消滅）。
