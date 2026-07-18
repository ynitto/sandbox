# intake 境界の調整とテストの新境界追随（r4 / t6）

**切り口: 「旧名が残っていない」は追随の必要条件でしかない。テストが実際に何かを掴んでいる証拠は、フックを 1 箇所ずつ壊した変異体でテスト単位の被覆行列を取り、新テスト全件が最低 1 つの変異体で赤くなることでしか出せない。実測したら 1 本が実プロバイダの存在で通っていた空振りだった。**

対象: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-81333-t3as3cgh/sandbox`（HEAD `d5e03f4`）

---

## 1. 変更

```
tools/agent-project/agent_project/model.py        |  21 +-
tools/agent-project/tests/test_agent_project.py   | 289 +++++++++++++++++++++++-
```

`agent_project` 配下で触ったのは `model.py` の `_parse_intake_records` 1 関数だけ。`hooks.py` / `doctor.py` / `configfile.py` は t3・t4・t5 の担当なので手を出していない。

### 1.1 intake 境界（`_parse_intake_records`）

実装契約 §3 の id / title 型正規化を入れた。JSON は id を数値で書けるが、`_gen_task_id` → `_slug_id` は `.strip()` を呼ぶ。正規化が無いと非文字列 id が `AttributeError` になり、`run_intake` の except（`ValueError` のみ）を抜けて watch ループごと落ちる。

| キー | 変更後 |
|---|---|
| `title` | `str(...).strip()` した値を spec に入れる（従来は検証のみで生値を素通し） |
| `id` | `str(...).strip()`。空なら spec からキーごと落とす（＝自動採番へ） |
| その他 | 素通し（変えない） |

`errors` の文言・分類、レコード単位に落とす方針、`run_intake` の冪等ロジックはいずれも変えていない。schemas/task 相当の汎用 JSON パースのまま、特定検出器の知識は持たない。

### 1.2 テストの新境界追随

| 区分 | 内容 |
|---|---|
| 改名 | `TestCoddGateNoAutoWiring` → `TestNoAutoWiring`、`test_configfile_has_no_codd_gate_auto_wiring_hook` → `test_configfile_has_no_auto_wiring_hook`。docstring の `codd_gate_regression.py` 言及も一般名へ。`hasattr(km, "_apply_codd_gate_auto_wiring")` の**アサート文字列は残した**（禁止する対象の名前そのもの。契約 §5 の指示どおり） |
| 新設 | `TestHookResolution`（10 本）— 能力表の固定、素の環境での sibling スキャン、設定明示の優先、フルキー > 前半キー、明示失敗でスキャンへ落ちないこと、契約不足 module の棄却、sibling 不在、前置フィルタ、キャッシュ（成功・`None` 両方） |
| 新設 | `TestDoctorWiringFindings`（7 本）— findings 素通し、注入引数の到達、無言縮退、片欠け不走行（両方向）、プロバイダ例外の吸収、明示指定失敗の warn、非 dict hooks の warn |
| 追加 | `TestIntake` へ 4 本 — パーサの正規化、非文字列 id の受理、非文字列 id の冪等、下流へ渡る spec が正規化済みであること |
| 共通 | `_HookTestBase` — `_HOOK_CACHE.clear()` と `sys.path` / `sys.modules` の巻き戻しを `addCleanup` で担保 |

注入点は設計メモ §7 のとおり `_hook_provider` に一本化した。`mock.patch.object(km, "_hook_provider", ...)` だけで doctor 側の全経路が差し替わる。走査対象ディレクトリの差し替えは `mock.patch.object(km, "__file__", ...)`（`_hook_provider` は sibling を `Path(__file__).resolve().parent.parent` で求め、exec 合成により `__file__` は共有名前空間の変数）。

---

## 2. 検証と結果

### 2.1 旧名が残っていないこと

```
$ grep -nE 'TestCoddGate|_codd_gate_wiring_module|doctor_codd_gate_findings|_codd_gate_debt_module|_wiring_module' \
    tools/agent-project/tests/test_agent_project.py
(なし)

$ grep -nE 'codd_gate' tools/agent-project/tests/test_agent_project.py
3943:        self.assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))
```

残る 1 行は「再導入を禁止する関数名」を書いた回帰ガードで、契約 §5 が明示的に残せと指示している箇所。

なお `_codd_gate_wiring_module` を mock/patch しているテストは**元から 1 件も存在しなかった**（t1 §5 の「配線経路にテスト 0 件」と整合）。タスク文中の `TestCoddGateAutoWiring` も実在せず、実在したのは自動配線の**不在**を固定する `TestCoddGateNoAutoWiring` のみ。したがって本タスクの実質は「追随」ではなく、空だった配線経路に新境界向けのテストを新規に敷くことだった。

### 2.2 テストが空振りでないこと（変異体 22 通り × テスト単位の被覆行列）

`hooks.py` / `doctor.py` / `model.py` を 1 箇所ずつ壊した変異体を作り、**テスト単位で**赤/緑を記録した。harness は `/tmp/t6-mutate2.py`、参照実装込みの検証ツリーは `/tmp/t6-sim/`。ベースライン 31 tests OK。

| # | 故意に壊した内容 | 赤くなったテスト |
|---|---|---|
| H1 | 前置フィルタを外す（全 sibling を無差別 import） | `test_scan_does_not_import_unrelated_siblings` |
| H2 | 設定明示の失敗で sibling スキャンへ落ちる | `test_explicit_miss_does_not_fall_back_to_scan` ほか 2 |
| H3 | キャッシュを効かせない | `test_resolution_is_cached` / `test_missing_provider_is_cached_too` |
| H4 | 契約チェックを外す | `test_explicit_module_missing_contract_is_rejected` |
| H5 | 前半キーがフルキーを上書きする | `test_full_capability_key_beats_prefix_key` |
| H6 | スキャン失敗時に特定 module 名へハード依存で戻る | `test_absent_sibling_dir_yields_none` ほか 1 |
| H7 | 能力表から findings 契約を落とす | `test_capability_table_pins_the_contract` ほか 1 |
| H8 | sibling スキャン自体を無効化する | `test_sibling_scan_finds_provider_in_real_tree` ほか 2 |
| H9 | 設定明示を無視して常にスキャンする | `test_config_hooks_win_over_sibling_scan` ほか 3 |
| D1 | 片方だけの解決でも走る（`or` → `and`） | `test_half_resolved_provider_does_not_run` |
| D2 | プロバイダ例外を握り潰さない | `test_provider_exception_does_not_break_doctor` |
| D3 | 注入引数 `which`/`run` を渡さない | `test_injected_args_reach_the_provider` |
| D4 | cfg の cmd を渡さない | `test_injected_args_reach_the_provider` |
| D5 | 明示指定の失敗を無言で縮退させる | `test_explicit_misconfig_is_reported_as_warn` ほか 1 |
| D6 | findings を素通ししない | `test_provider_findings_pass_through` |
| D7 | プロバイダ不在でも所見を出す | `test_no_provider_and_no_config_is_silent` ほか 3 |
| D8 | 非 dict hooks で例外を投げる | `test_non_dict_hooks_is_reported_as_warn` |
| D9 | 非 dict hooks の判定分岐ごと落とす | `test_non_dict_hooks_is_reported_as_warn` |
| M1 | title の正規化を外す | `test_parse_intake_records_normalizes_id_and_title` ほか 1 |
| M2 | id の文字列化を外す | 上記＋`test_run_intake_accepts_non_string_id` ほか 2 |
| M3 | id の strip を外す | `test_parse_intake_records_normalizes_id_and_title` ほか 1 |
| M4 | 空 id をキーごと落とさない | 同上 |
| M5 | 入力 dict のコピーをやめる | **緑のまま**（等価変異。§2.3 に理由） |

**t6 が新設した 21 本すべてが最低 1 つの変異体で赤。** 一度も赤にならなかった 10 本はいずれも t6 が書いていない既存テスト（`TestNoAutoWiring` の configfile 自動配線不在 4 本、`TestIntake` / watch の既存 6 本）で、フックの変異面と主題が交わらないため。

### 2.3 実測で潰した空振り 3 件

被覆行列は一発で埋まっていない。3 本を書き直している。

1. **`test_non_dict_hooks_degrades_without_raising` → `test_non_dict_hooks_is_reported_as_warn`（今回修正）** — D8（非 dict で例外を投げる）でも D9（分岐ごと削除）でも緑のまま残った。原因は実プロバイダの存在。非 dict `hooks` でも `_hook_provider` は sibling スキャンで実 module を引き当てて成功するため `_hook_misconfig_findings` へ入らず、`assertIsInstance(got, list)` が「型不正を扱えている」ではなく「たまたま所見が返った」で通っていた。`mock.patch.object(km, "_hook_provider", lambda cap, cfg=None: None)` で解決を落とし、warn 1 件と title を固定するよう書き直した。D5 / D7 / D8 / D9 の 4 通りで赤になる。

2. **`test_run_intake_trims_and_autonumbers_blank_id`（削除）** — `_slug_id` が下流で既に `strip()` しているので、`id: "  x  "` は正規化の有無に関わらず `"x"` になる。契約 §5 のケース 2・3 は `run_intake` のレベルでは変更前後で振る舞いが同じで、差が出るのは `_parse_intake_records` の返り値だけ。代わりに `enqueue_task` の spy で下流へ渡る spec を見る `test_run_intake_hands_normalized_specs_downstream` を置いた（M1〜M4 で赤）。

3. **`test_half_resolved_provider_does_not_run`（書き直し）** — 戻り値だけを見ていたため、片欠けのまま走っても `except Exception` に吸われて空リストになり「走らなかった」と区別できなかった。`detect_wiring` が**呼ばれたか**を観測するよう書き直し、`subTest` で両方向を回す。

M5（入力 dict のコピーをやめる）が緑のまま残るのは**等価変異で、テストの穴ではない**。`_parse_intake_records` は text を受け取って内部で `json.loads` するため、パース済み dict への参照を外部の呼び出し側が持つ経路が無く、`spec = dict(raw)` と `spec = raw` の差は関数の外から観測できない。ここに固定テストを足しても必ず緑になる空振りなので、意図的に書いていない（`dict(raw)` は防御的なスタイルであって契約ではない）。

### 2.4 テスト実行

worktree 単体（`hooks.py` 未着地の統合前状態）:

```
$ PYTHONPATH=. python3 tests/test_agent_project.py TestIntake TestNoAutoWiring
Ran 14 tests in 0.557s
OK

$ PYTHONPATH=. python3 tests/test_agent_project.py           # 全体
Ran 735 tests in 294.276s
FAILED (failures=3, errors=17)
```

- **failures=3** は `TestDaemonRouting.test_kf_base_passes_flow_config` / `TestJournalRotation.test_rotation_archives_and_starts_fresh` / `TestProjectLayer.test_version_inherits_master_charter`。契約 §5 が「この 3 件だけが残る状態を合格」とした main 由来の既存 failure。
- **errors=17** は `TestHookResolution` 10 + `TestDoctorWiringFindings` 7 の全件で、原因はすべて `AttributeError: module 'agent_project' has no attribute '_HOOK_CACHE'`。t3 の `hooks.py` と t4 の `doctor` が入れば解消する統合前の想定内の赤で、他のテストは 1 件も壊していない。

参照実装ツリー（契約 §1・§2・§4 の逐語から `hooks.py` / `doctor.py` を組んだもの）では全件緑:

```
$ cd /tmp/t6-sim/agent-project
$ PYTHONPATH=. python3 tests/test_agent_project.py \
      TestHookResolution TestDoctorWiringFindings TestIntake TestNoAutoWiring
Ran 31 tests in 0.592s
OK
```

---

## 3. 採用した前提

1. **`_parse_intake_records` の正規化を t6 で実装した。** 契約 §3 はこれを model 実装者（t5）に割り当てているが、t5 の goal 文は `_codd_gate_debt_module` の除去（r0 で実施済み）に向いており、正規化が落ちる可能性がある。一方 t6 の goal は「id による冪等 enqueue の振る舞いを維持」を明示していて、この関数は intake の境界そのもの。知っていて赤いテストを渡すより実装する方が妥当と判断した。**t5 が同じ修正を入れると `model.py` で衝突する**ので、統合時に確認が要る（内容は契約 §3 の逐語なので、どちらが残っても振る舞いは同じ）。
2. **`TestCoddGateNoAutoWiring` は改名した。** 契約 §5 は「改名は任意」とするが、t6 の goal が成果物として「旧名が残っていないこと」を要求しているので字義どおり従った。
3. **非 dict `hooks` の warn は契約 §4-3 の逐語どおり固定した。** 前回の報告はこの分岐を「`build_config` が `{}` へ落とすので到達不能」として弱いアサートに逃がしていたが、これは誤り。到達不能なのは `build_config` 経由の場合だけで、`doctor_wiring_findings` にプロバイダ不在の cfg が渡れば分岐は生きる。契約どおり warn を固定した（t4 がこの分岐を実装しないと赤くなる）。
4. **`cfg` は `types.SimpleNamespace(hooks=...)` で代用した箇所がある。** `Config` に `hooks` を足すのは t3 の担当。解決ロジックだけを見るテストは SimpleNamespace で書き、`Config` 経由が要る `TestDoctorWiringFindings` は `cfg_for(d, hooks=...)` を使っている（＝t3 のフィールド追加を暗黙に固定する）。
5. **`hooks.py` の参照実装は成果物に含めない。** 検証のためだけに `/tmp/t6-sim/` に置いた。worktree へは書いていない（t3 との衝突を避けるため）。

---

## 4. 未解決事項・範囲外で見つけた問題

- **[統合時の要確認]** 上記前提 1 の `model.py` 衝突。t5 の成果と突き合わせること。
- **[範囲外・未実施]** 契約 §6-5 の「変更前後で `doctor_wiring_findings` の出力が 1 文字も変わらない」実測は、`doctor.py` を触る t4 の担当。t6 は `doctor` に触れていないので出力に影響しない。
- **[範囲外]** 既存 3 failures（`TestDaemonRouting` / `TestJournalRotation` / `TestProjectLayer`）は t1 が main 由来と切り分け済み。触っていない。
- `@followup agent_project の hooks 設定を非 dict で書いたときの挙動を build_config の握り潰しと doctor の warn の二重防御のままにするか一本化するか決める :: PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestDoctorWiringFindings`
