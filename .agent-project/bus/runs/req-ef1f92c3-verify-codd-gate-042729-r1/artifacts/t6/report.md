# verify-codd-gate-042729 t6 検証レポート

## 判定
verify=fail

## 完了条件コマンドの再実行（独立検算）

実行コマンド:

```bash
cd /Users/nitto/Workspace/sandbox && PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
```

結果: exit code 0

## 既存テスト実行（非退行確認）

実行コマンド:

```bash
cd /Users/nitto/Workspace/sandbox && PYTHONPATH=tools/agent-project python3 -m pytest tools/agent-project/tests/test_agent_project.py
```

失敗出力（そのまま）:

```text
============================= test session starts ==============================
platform darwin -- Python 3.14.2, pytest-9.0.2, pluggy-1.6.0
Using --randomly-seed=456188765
rootdir: /Users/nitto/Workspace/sandbox
plugins: cov-7.1.0, randomly-4.1.0
collected 667 items

tools/agent-project/tests/test_agent_project.py ........................ [  3%]
........................................................................ [ 14%]
........................................................................ [ 25%]
........................................................................ [ 35%]
........................................................................ [ 46%]
........................................................................ [ 57%]
........................................................................ [ 68%]
........................................................................ [ 79%]
........................................................................ [ 89%]
................................................F..................      [100%]

=================================== FAILURES ===================================
______________ TestDaemonRouting.test_kf_base_passes_flow_config _______________

self = <test_agent_project.TestDaemonRouting testMethod=test_kf_base_passes_flow_config>

    def test_kf_base_passes_flow_config(self):
        """sync run / submit / doctor も daemon と同じ flow_config（--config）を渡す。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            yaml = d / "agent-flow.yaml"
            yaml.write_text("executor: stub\n", encoding="utf-8")
            c = cfg_for(d, flow_config=str(yaml))
            base = km._kf_base(c, False)
            self.assertIn("--config", base)
>           self.assertEqual(base[base.index("--config") + 1], str(yaml.resolve()))
E           AssertionError: '/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc00[30 chars]yaml' != '/private/var/folders/8c/s6jh85ls4tq3fmzkl0[38 chars]yaml'
E           - /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/tmp5i28e5sy/agent-flow.yaml
E           + /private/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/tmp5i28e5sy/agent-flow.yaml
E           ? ++++++++

/Users/nitto/Workspace/sandbox/tools/agent-project/tests/test_agent_project.py:2997: AssertionError
=========================== short test summary info ============================
FAILED tools/agent-project/tests/test_agent_project.py::TestDaemonRouting::test_kf_base_passes_flow_config
================== 1 failed, 666 passed in 113.45s (0:01:53) ===================
```

## 検証観点チェック

1. 完了条件コマンド: 充足（exit 0）
2. 集計整合: `collected 667`, `1 failed, 666 passed` で整合
3. 抜け漏れ/重複: 失敗は1件に集約（summary と一致）
4. 抜き取り妥当性: 失敗箇所は `/var` と `/private/var` の絶対パス比較不一致
5. スコープ外差分: `tools/agent-project/agent_project/verify.py` 以外にも `test_agent_project.py` 等の変更が存在

{"ok": false, "issues": ["tools/agent-project/tests/test_agent_project.py::TestDaemonRouting::test_kf_base_passes_flow_config が /var と /private/var の実パス差で失敗。_kf_base 側またはテスト側で realpath/resolve の基準を統一し、macOS シンボリックリンク差を吸収する必要がある。"]}
