# t11-codd-gate-end-to-end-verify

判定: **verify=fail**

## 1) 完了条件コマンド（grep）の独立再実行

対象を専用 worktree（`/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/kiro-worktree-91tidjag`）で再検証。

```bash
grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
```

実行結果:

- exit code: `0`
- 一致行:
  - `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`

補足: 同ファイルで `intake_cmd` も併記されていることを確認。

---

## 2) 自動検出の結線（設定生成・更新経路）を批判的に再導出

### 2-1. 設定生成（`build_config` 経路）

`.agent/agent-project.yaml` を置かないクリーンな一時ディレクトリに `repos.json` だけ置いて、
`resolve_config(args)` → `build_config(args)` を実行。

再現コマンド（要旨）:

```python
args = Namespace(root='.', config=None)
args = km.resolve_config(args)
cfg = km.build_config(args)
print(cfg.regression_cmd, cfg.intake_cmd)
```

実行結果:

- `config_path=None`
- `regression_cmd=None`
- `intake_cmd=None`

**観測**: `repos.json` が存在しても、`build_config` で `regression_cmd` / `intake_cmd` は自動設定されない。

### 2-2. 設定更新（`.agent/agent-project.yaml` への自動注入経路）

期待される更新モジュールの存在確認:

```bash
test -f tools/agent-project/codd_gate_regression.py; echo $?
test -f tools/agent-project/codd_gate_wiring.py; echo $?
```

実行結果:

- `codd_gate_regression.py`: `1`（存在しない）
- `codd_gate_wiring.py`: `1`（存在しない）

**観測**: `.agent/agent-project.yaml` 更新を担う自動注入経路を再現できる実装ファイルが、この検証対象 worktree では確認できない。

---

## 3) 連携有効性（intake 側の実接続）再検証

`parse_debt_output` の参照点を検索すると、`codd_gate_debt.py` の定義以外の呼び出しが見つからない。

（`agent_project/model.py` の `run_intake` は JSON を直接 `json.loads` し、`codd_gate_debt.parse_debt_output` を呼ばない実装）

**観測**: codd-gate debt 出力のレコード単位検証が、実行経路へ結線されている証拠を確認できない。

---

## 4) テストによる裏取り

- `python3 -m pytest -q tools/agent-project/tests/test_codd_gate_routing.py` → `9 passed`
  - ただし当該テスト自身が「`.agent/agent-project.yaml` の値を素の YAML 読み書きで確認し、実際の自動配線は別タスク担当」と明記。
- `python3 -m pytest -q tools/agent-project/tests/test_agent_project.py -k 'run_intake and codd_gate'` → `663 deselected`（exit 5）
  - runtime 結線を直接担保するテストはこの条件では確認できず。

---

## 5) 判定

- 完了条件 grep は **満たす**（exit 0）。
- ただし要求された「自動検出が設定生成・更新経路から `regression_cmd`/`intake_cmd` の双方へ結線され、実際に有効」の裏付けは、この検証対象では **満たせない**。

したがって総合判定は **verify=fail**。

## issues

1. **設定生成経路未結線**: `agent_project.configfile.build_config` は、`repos.json` が存在しても `cfg.regression_cmd`/`cfg.intake_cmd` を自動設定しない（再現済み）。`build_config` から codd-gate 検出・推奨コマンド注入の処理を接続する必要がある。
2. **設定更新経路未再現**: `tools/agent-project/codd_gate_regression.py`（および `codd_gate_wiring.py`）が存在せず、`.agent/agent-project.yaml` を自動更新する経路を検証不能。更新責務の実装配置または参照先を明示し、再現可能にする必要がある。
3. **intake 側の実連携不足**: `run_intake` から `codd_gate_debt.parse_debt_output` への呼び出しが確認できない。debt JSON のレコード単位検証を実行経路に接続し、失敗時の扱いを仕様どおりに固定する必要がある。
