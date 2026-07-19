# doctor の findings 等価性実測（r4 / t4）

対象: `tools/agent-project/agent_project/doctor.py`（変更はこの1ファイルのみ）

before = このブランチの HEAD（38a1ccd8）の doctor.py。after = 変更後の作業ツリー。
プロバイダ以外の条件を揃えるため、両方を同じ手順で組んだツリーへ配置して測った。

## 測定の組み方

```
/tmp/tree_{before,after}_{present,absent}/
    agent_project/          # before は HEAD の doctor.py を上書き
    codd_gate_*.py          # present のみ配置。absent は agent_project だけ
```

`present` = sibling に配線プロバイダが実在する環境、`absent` = `codd_gate_wiring` /
`codd_gate_regression` が import 経路のどこにも無い環境。root は固定パス（`/tmp/probe_root`）に
した。tempfile を使うと finding の `fix` に一時ディレクトリ名が混ざり、実体が同じでも差分が出る。

プローブは `doctor_wiring_findings(cfg, which=...)` を `which=None`（codd-gate 未検出）と
`which="/usr/bin/true"`（検出されるがバージョン取得に失敗）の2条件で呼び、`count` と findings
全体を `sort_keys=True` の JSON で出力する。件数だけでなく各 finding の
category / severity / title / evidence / fix まで比較対象に含まれる。

## 結果: 4通りすべて完全一致（diff 空）

| 環境 | which | before | after | diff |
|---|---|---|---|---|
| present | None | 1件 `info` / codd-gate が見つからない（PATH・同梱パスのいずれにも無い） | 同一 | 空 |
| present | found | 1件 `warn` / codd-gate のバージョンを取得できない | 同一 | 空 |
| absent | None | 0件 | 0件 | 空 |
| absent | found | 0件 | 0件 | 空 |

内容・件数・出力順のいずれも変化なし。finding の文言に `codd-gate` が出るのはプロバイダ側が
持つ文字列で、本体から渡した名前ではない。

## degrade（プロバイダ不在で例外を出さない）

`doctor_wiring_findings` 単体だけでなく、実際の CLI 経路でも確認した。

```
PYTHONPATH=/tmp/tree_after_absent python3 -c "
import sys, agent_project as km
sys.argv = ['agent-project', 'doctor', '--root', '/tmp/docroot', '--json']
print('exit code:', km.main())"
```

`absent` / `present` とも例外なく最後まで走り、JSON を出力して exit code 1
（空 root なので `unresolved: 8`。異常終了ではなく所見ありの意味）。両環境で
`unresolved` は同数。

## 分岐ごとの実測（契約 §4 / §5 の期待との突き合わせ）

`present` 環境で `_HOOK_CACHE` を差し替えて各経路を通した。

| ケース | 期待 | 実測 |
|---|---|---|
| 明示指定 `hooks.wiring = "no_such_module"` が解決不能 | `warn` 1件 | 1件 `warn` / 指定した配線プロバイダを解決できない |
| `hooks` が dict でない | `warn` 1件 | `warn` 1件 + プロバイダ由来 info 1件 |
| 未指定でプロバイダ不在 | 空・無言 | 0件 |
| 片方の能力だけ解決 | 空 | 0件 |
| プロバイダが `RuntimeError` を投げる | 空・doctor は落ちない | 0件・例外なし |
| 注入引数の到達 | which/run/repos_path/regression_cmd/intake_cmd | 5つとも到達を確認 |

## テスト

```
cd tools/agent-project && PYTHONPATH=. python3 tests/test_agent_project.py
  -> Ran 714 tests in 296.203s / FAILED (failures=3)
```

失敗は `TestDaemonRouting.test_kf_base_passes_flow_config` /
`TestJournalRotation.test_rotation_archives_and_starts_fresh` /
`TestProjectLayer.test_version_inherits_master_charter` の3件のみ。t2 §5 が main 由来として
スコープ外に置いた3件と一致し、それ以外の増加はない。

対象クラス単体:
```
PYTHONPATH=. python3 tests/test_agent_project.py TestCoddGateNoAutoWiring TestIntake TestLoopEngineering
  -> Ran 22 tests / OK
```

## grep

```
git grep -nE 'codd_gate' -- tools/agent-project/agent_project                     -> 0件
git grep -nE '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
    -- tools/agent-project/agent_project                                          -> 0件
```

厳格 grep の残り3行（`doctor.py:288,302,324`）が本タスクで消え、パッケージ内の
`codd_gate` は 0 になった。
