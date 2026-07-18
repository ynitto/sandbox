# task `doc` 成果: doctor から codd_gate 名を除去

対象ファイル: `tools/agent-project/agent_project/doctor.py`（このタスクの唯一の書込先）

## 改名（旧 → 新）

| 旧シンボル | 新シンボル |
|---|---|
| `_codd_gate_wiring_module` | `_wiring_module` |
| `doctor_codd_gate_findings` | `doctor_wiring_findings` |

`doctor_*_findings` の命名規約（`doctor_env_findings` / `doctor_audit_findings` /
`doctor_flow_bus_coverage_findings`）に合わせ、subject を `codd_gate` → `wiring` に汎用化。

## 差し込み点化（本体無改造・差し込み点のみ）

- `import codd_gate_wiring`（直 import 文）を撤去し、`importlib.import_module("codd_gate_wiring")`
  で遅延・動的解決に変更。→ `git grep "import codd_gate"` が doctor.py で 0 件。
- docstring の `_codd_gate_debt_module` 参照（`_codd_gate` grep ヒット源）を汎用文へ書き換え。
- sibling プロバイダの**実ファイル名** `codd_gate_wiring`（= `tools/agent-project/` 直下、
  `agent_project/` の外なので受入 grep 対象外）は文字列としてのみ残置。`cfg` タスクが
  「sibling（codd_gate_wiring / codd_gate_regression）呼び出し」で残すのと同じ方針。

## 受入 grep（`tools/agent-project/agent_project/` スコープ, doctor.py 内で確認済み）

`import codd_gate` / `_apply_codd_gate` / `_codd_gate` … いずれも **0 件**。

## 後続タスクへの引き継ぎ（doctor.py 外・当タスクは触っていない）

- **test タスク**: `tests/test_agent_project.py` の `TestCoddGateAutoWiring`
  （行 3894 / 3923 / 3933）が `mock.patch.object(km, "_codd_gate_wiring_module")` を使用。
  → `_wiring_module` へ付け替え要（当タスクの改名に追随。deps に doc を含む正規の担当）。
  現状この6件が AttributeError で fail するのは想定内。
- **cfg タスク**: `configfile.py:220` の `wiring = _codd_gate_wiring_module()` が旧名を呼ぶ。
  cfg は `_apply_codd_gate_auto_wiring` を除去/外出しするため、この参照は cfg 側で解消される想定
  （呼び出しは実行時のみ＝package import は現状でも成功する）。cfg が呼び出しを残す設計なら
  `_wiring_module` へ改名要。
- **@followup (synth 判断)**: doctor `_wiring_module` と model 側の debt ローダは同型の
  sibling 解決を行う。synth 段で共有ヘルパ（例: `_sibling_module(name)`）へ DRY 統合する余地あり。
  当タスクでは cross-fragment 結合を避けるため doctor 内に自己完結実装として留めた。
