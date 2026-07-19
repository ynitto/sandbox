# 新テストが空振りでないことの実測（r4 / t6）

再現: `python3 mutation_probe.py <repo>/tools/agent-project`
（各ミューテーションを適用 → 対象テストだけ実行 → `finally` で原文へ復元し、復元をバイト比較で assert）

13 ミューテーション全部で対象テストが赤。緑のまま通ったものは無い。

| ID | 壊した性質 | ファイル | 赤になったテスト |
|---|---|---|---|
| M1 | sibling 走査の前置フィルタを外す | hooks.py | `test_prefilter_does_not_import_unrelated_siblings` |
| M2 | 明示指定の解決失敗で自動検出へ落ちる | hooks.py | `test_configured_name_that_fails_does_not_fall_back` / `test_configured_provider_that_fails_is_reported` |
| M3 | 必須属性の検査を外す | hooks.py | `test_configured_module_without_contract_is_rejected` |
| M4 | 解決結果をキャッシュしない | hooks.py | `test_result_is_cached_including_misses` |
| M5 | 設定の明示指定を読まない | hooks.py | `test_configured_name_wins_over_sibling_scan` |
| M6 | sibling 走査が常に不発 | hooks.py | `test_sibling_scan_resolves_provider_by_capability` |
| M7 | 片方の能力だけでプロバイダ経路へ入る | doctor.py | `test_half_resolved_capability_does_not_call_provider` |
| M8 | プロバイダ由来の例外を畳まない | doctor.py | `test_provider_exception_does_not_break_doctor` |
| M9 | 注入引数を渡さない | doctor.py | `test_injected_arguments_reach_the_provider` |
| M10 | 設定ミスを所見にしない | doctor.py | `test_configured_provider_that_fails_is_reported` / `test_broken_hooks_type_is_reported` |
| M11 | intake の id を型正規化しない | model.py | `test_run_intake_normalizes_non_string_id` ほか 2 件 |
| M12 | 空白だけの id をキーごと落とさない | model.py | `test_parse_intake_records_normalizes_title_and_id` |
| M13 | title を strip しない | model.py | `test_parse_intake_records_normalizes_title_and_id` |

## 初回に空振りだった 2 件（テストを直した）

最初の実行で M7・M12 が緑になり、テストが弱いことが露見した。両方ともテスト側を直して赤にした。

**M7**: `if detect is None or render is None` を `and` に変えても結果は `[]` のままだった。片方が `None` の状態で先へ進むと `None.detect_wiring` が `AttributeError` を投げ、直後の `except Exception` が畳んでしまうため、返り値だけ見ても差が出ない。観測できる差は「プロバイダを呼んだかどうか」しかない。`detect_wiring` は環境を probe する副作用付きの関数なので、揃っていない時点で呼ばないことをテストの主張に格上げした（`seen == {}`）。

**M12**: 空白だけの id をキーごと落とさず残しても、`run_intake` 経由では `_slug_id` が空へ潰して自動採番に倒れ、振る舞いが変わらなかった。この性質は `_parse_intake_records` の出力そのものにしか現れないため、パーサ境界の等値テスト（`test_parse_intake_records_normalizes_title_and_id`）を足して固定した。

## 旧注入点名の残存

```
git grep -nE 'codd_gate' -- tools/agent-project/agent_project                 # 0 hit
git grep -nE '_codd_gate_wiring_module|_wiring_module|_apply_codd_gate|TestCoddGateAutoWiring' \
    -- tools/agent-project/tests                                             # 1 hit
```

tests 側の 1 hit は `test_configfile_has_no_codd_gate_auto_wiring_hook` の
`assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))`。禁止対象の名前を書いた否定アサート
なので残すのが正しい（t2 実装契約 §5 も同じ判断）。

`_codd_gate_wiring_module` を patch するテストは r4 の HEAD 時点で既に存在しない
（r0 で除去済み）。置き換え対象が無かったので、新境界に対するテストを新設する形で埋めた。

## テストスイート

| | 件数 | 失敗 |
|---|---|---|
| 変更前（t4 まで） | 714 | 3（`TestDaemonRouting.test_kf_base_passes_flow_config` / `TestJournalRotation.test_rotation_archives_and_starts_fresh` / `TestProjectLayer.test_version_inherits_master_charter`） |
| 変更後 | 733（+19） | 同じ 3 件のみ |

## プロバイダ実解決の end-to-end 確認

```
$ PYTHONPATH=tools/agent-project python3 /tmp/t6_wiring_probe.py
hooks = {}
detect provider = codd_gate_wiring
[{"category": "config", "severity": "info",
  "title": "codd-gate は検出済みだが regression_cmd が未結線", ...}]
```

hooks 未指定の素の環境で能力スキャンが実プロバイダへ解決し、所見も出続けている。所見文言の
`codd-gate` はプロバイダ側が持つ文字列で、パッケージ内には無い。
