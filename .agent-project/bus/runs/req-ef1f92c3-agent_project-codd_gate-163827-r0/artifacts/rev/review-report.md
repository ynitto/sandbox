# 境界レビュー — agent_project の codd_gate 非依存化（ap/agent_project-codd_gate-163827 @ 65d32ee1）

対象差分: `git diff 9a7302f..65d32ee1 -- tools/agent-project`（7 ファイル、+86/-148）
書込許可: `tools/agent-project` 配下のみ。実際に変更したのは 2 ファイル（README.md / agent_project/doctor.py）。

## 結論

境界の設計意図（本体は差し込み点＝設定キーのみ、実装は sibling へ外出し）はコードで成立している。
修正したのは 2 件——指示された README の実装不一致 1 件と、観点 (2) で実測した縮退漏れ 1 件。
残りは不採用（理由付きで後述）または `@followup`。

## 観点別の所見

### (1) パッケージ本体の codd-gate 依存 — 合格（残り 1 箇所は followup）

`agent_project/` 配下に `import codd_gate*` は 0 件（受入 grep と同じ条件で確認済み）。
差し込み点は `regression_cmd` / `intake_cmd` の設定キーに閉じており、`configfile.py:119` と
`configfile.py:467` の codd-gate 言及はいずれもヘルプ文字列・コメント中の**用例**で、実行時依存ではない。

唯一コードとして残る固有名は `doctor.py:296` の `provider = "codd_gate_wiring"`（文字列リテラル）。
指示どおり設定キー化は行わず followup に回した。

### (2) `doctor._wiring_module()` の no-op 縮退 — **欠陥を検出・修正した**

観点は「sibling 不在・import 失敗・属性欠落の**いずれでも**空リストへ縮退するか」だが、
修正前は **3 つのうち 1 つ（sibling 不在＝ImportError）しか縮退しなかった**。実測で確認:

```
A attr-missing  -> RAISED AttributeError : module 'codd_gate_wiring' has no attribute 'detect_wiring'
B import-raises -> RAISED ValueError : boom at import
```

- **属性欠落**: sys.path 先頭に同名の無関係な module が居ると `import_module` は成功し、
  `doctor_wiring_findings` の `wiring.detect_wiring(...)` で `AttributeError` が送出される。
- **import 失敗（非 ImportError）**: プロバイダ自身が import 時に例外を投げると `except ImportError`
  を素通りして伝播する。docstring は「見つからない・**解決失敗**のときは None」と書いており、
  コードが自分の契約を満たしていない。

いずれも `cmd_doctor` の `deterministic = (... + doctor_wiring_findings(cfg))` を経由して
**doctor コマンド全体を落とす**。診断コマンドが任意連携の不備で死ぬのは失う情報が桁違いに大きい。

これは本差分が新規に作った欠陥ではない（旧 `_codd_gate_wiring_module` も ImportError のみ捕捉）。
ただし `_apply_codd_gate_auto_wiring` 除去で **doctor が唯一の結線導線になった**ため、観点 (2) が
明示的に問う 3 ケースの担保は本差分の完了条件の一部と判断して修正した。

修正（`doctor.py`）: import を `except Exception` へ広げ、返す前に本体が呼ぶ関数
`("detect_wiring", "doctor_findings")` の存在を検証する。どちらか欠ければ None。
sibling を sys.path へ足して 1 回だけ再試行する構造は元のまま維持した。

修正後の実測:

```
A attr-missing  -> OK, findings = []
B import-raises -> OK, findings = []
実プロバイダ    -> <module 'codd_gate_wiring' from '.../tools/agent-project/codd_gate_wiring.py'>
                   findings = 1 件（「codd-gate は検出済みだが regression_cmd が未結線」）
```

正常系の解決経路と finding 出力は不変（同じ module オブジェクトを返し、呼ぶ関数も同じ）。

### (3) `model._parse_intake_records` の検出器非依存性 — 合格

依存しているのは `schemas/task.schema.json` の required である `title` と「レコードが object であること」
の 2 点のみ。codd-gate 固有フィールド（drift 種別・repo 名など）への言及は無い。
トップレベルの object / array 両対応、空文字は 0 件、1 件の不備は当該レコードだけ errors へ落として
残りを通す——旧 `codd_gate_debt.parse_debt_output` の契約と同値。

`id` 冪等も保たれている。`run_intake` 側の `_slug_id(str(sp.get("id", "") or ""))` が
旧 `DriftItem.to_spec()` の正規化（int→str、空文字→キー省略）と同じ結果に落ちることを確認した。

非 JSON 時の挙動は文言のみ変化（journal が「intake 出力が JSON でないため無視」→
「intake レコード無効: JSON として解釈できない: …」）。戻り値は両方 `[]` で、
`test_run_intake_tolerates_failures` が引き続き担保する。

**不採用にした指摘**: 旧経路の `to_spec()` は `title` を `.strip()` していたが、新経路は生の dict を
そのまま `enqueue_task` へ渡すため、`"  ok  "` のような前後空白が保存される。
汎用パススルーのフックとしてはレコードを加工しない方が正しく、title の正規化責務は `enqueue_task`
側にある。ここで strip を足すと enqueue 側の意味論に踏み込むスコープ超過になるため直さない。

### (4) 除去シンボルへのダングリング参照 — 合格（1 件は範囲外）

4 シンボル（`_apply_codd_gate_auto_wiring` / `_codd_gate_wiring_module` /
`doctor_codd_gate_findings` / `_codd_gate_debt_module`）をリポジトリ横断で grep。ヒットは 2 件のみ:

- `tests/test_agent_project.py:3893` — `assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))`。
  再導入ガードで**意図的**。呼び出しではない。
- `docs/designs/codd-gate-design.md:286` — 現存機能として記述しており陳腐化。書込許可外のため
  未修正（followup）。

呼び出し側のダングリングは 0 件。`cmd_doctor` の呼び出しも `doctor_wiring_findings` へ追随済み。

### (5) `TestCoddGateAutoWiring` → `TestCoddGateNoAutoWiring` の入れ替え — 失われた担保あり（followup）

旧 6 件のうち 4 件は「自動配線が値を埋める」振る舞いのテストで、機能ごと除去されたため
カバレッジ喪失にはあたらない。新 4 件は反転した不変条件（埋めない・素通しする・関数が再導入されない）
を固定していて、置き換えとして妥当。

ただし旧 `test_wiring_module_unavailable_is_a_noop` が担保していた**「プロバイダ不在なら no-op」**は、
移設先の `doctor` 側にテストが無く**純減している**（移設前後とも `doctor_wiring_findings` /
`_wiring_module` の直接テストは 0 件）。指示により追加はせず followup とした。
なお今回の (2) の修正は**既存テストでは検出されない**ため、上記の実測スクリプトで担保した。

## 検証

### 受入コマンド — exit 0

```
PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py \
  TestIntake.test_run_intake_enqueues_and_dedups_by_id \
  TestLoopEngineering.test_regression_gate_blocks_on_failure \
  TestLoopEngineering.test_regression_gate_passes \
&& ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
     -- tools/agent-project/agent_project
→ Ran 3 tests in 0.376s / OK / grep 0 hit / FINAL EXIT CODE: 0
```

### フルスイート — pre-existing の 3 件から増えていない

```
Ran 714 tests in 310.953s
FAILED (failures=3)
  FAIL: test_kf_base_passes_flow_config (TestDaemonRouting)
  FAIL: test_rotation_archives_and_starts_fresh (TestJournalRotation)
  FAIL: test_version_inherits_master_charter (TestProjectLayer)
```

件数・失敗テスト名とも依存タスク（`verify-report-independent.md`）が merge-base `9a7302f` と
HEAD の両側で実測した 3 件と完全一致。本修正による新規失敗は 0。

### 縮退の実測

`/tmp/repro_wiring.py` で属性欠落・import 時例外・実プロバイダの 3 ケースを直接実行（結果は観点 (2) に転記）。

## 採用した前提

- 観点 (2) の「属性欠落でも縮退するか」は**担保すべき完了条件**として読み、実測で欠陥を確認した以上
  修正対象に含めた（followup 指定は「`_wiring_module` の**ユニットテスト追加**」であって、
  縮退そのものの堅牢化は除外リストに無いため）。テスト追加は指示どおり行っていない。
- `except Exception` の握り潰しは finding を出さず沈黙させた。doctor の出力を増やすと既存テストの
  期待に触れうるうえ、no-op 縮退という文書化済みの契約に沿うのはこちらのため。
- README 修正は指示どおり README 側のみ。`codd_gate_regression.py` への `intake_cmd` upsert 追加は
  sibling の仕様変更にあたるため採らない。

## 範囲外で見つけた問題（未修正）

```
@followup docs/designs/codd-gate-design.md の陳腐化を解消する :: L271/L277 が codd_gate_debt.parse_debt_output を intake 本線パーサとして、L286 が _apply_codd_gate_auto_wiring を現存機能として記述している。README §4.1 がここを参照しているため放置すると誤読が続く。docs/ 書込許可付きのタスクで追随する
@followup doctor.py:296 の provider 名を設定キーへ逃がす :: provider = "codd_gate_wiring" がパッケージ内に残る唯一の codd_gate 固有名。wiring_provider（既定 "codd_gate_wiring"）等の設定キーにすればコードでなくデータになり「本体に固有名を残さない」をより満たす
@followup doctor の配線導線にユニットテストを足す :: _wiring_module が None → doctor_wiring_findings が空リスト／repos.json あり → 未結線 finding が 1 件、の 2 ケース。加えて今回修正した属性欠落・import 時例外の縮退も回帰テスト化する（現状スクリプト実測のみ）
@followup pre-existing failure 3 件を解消する :: TestDaemonRouting.test_kf_base_passes_flow_config / TestJournalRotation.test_rotation_archives_and_starts_fresh / TestProjectLayer.test_version_inherits_master_charter。merge-base から存在し本差分とは無関係
@followup codd_gate_debt.parse_debt_output の実利用者を再確認する :: agent_project/ から参照されなくなり、実利用者は自身のテストのみ。docstring に「独立したアダプタとして残る」と意図的残置が明記されているので現状維持でよいが、次に触る際に棚卸しする
```

## 変更ファイル

| ファイル | 変更 | 根拠 |
|---|---|---|
| `tools/agent-project/README.md` | L279-286 を regression_cmd 限定の記述へ。intake_cmd の恒久設定は設定ファイル直接編集と併記 | 指示の必須 1 件。`codd_gate_regression.py:44` の `KEY = "regression_cmd"` が 1 キーのみ upsert、同 L23 が intake_cmd 注入を別責務と明記 |
| `tools/agent-project/agent_project/doctor.py` | `_wiring_module()` を import 時例外・属性欠落でも None へ畳むよう堅牢化 | 観点 (2)。実測で 2 ケースの縮退漏れを確認 |

いずれも `tools/agent-project` 配下。範囲外の差分は無い。
