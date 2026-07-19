# intake 境界とテストの新境界追随（r4 / t6）

**切り口: 「旧名が残っていない」は空振りしていない証拠にならない。テスト 1 本ずつの被覆行列を変異解析で取り、新テスト 24 本すべてが最低 1 つの変異で赤くなることを実測した。最初の 13 変異では 4 本が誰にも殺されず残ったので、その 4 本が守っている性質を壊す変異を後から足して埋めた。あわせて、受入の grep を 1 回きりのゲートから常設の回帰ガードへ移した（名前は次の変更で戻ってくる）。**

作業ツリー: `/var/folders/8c/.../agent-flow-ws-15863-kgpbpfhz/sandbox`（HEAD `11ef6b21` = t5）
検証環境: ブランチ先端 `38a1ccd8`（= t3 の `hooks.py` を含む）を `git_worktree.py provision` で取得し、`/tmp` の scratch へ複製して実行（共有チェックアウトへは書き込んでいない）。

---

## 1. 変更

```
tools/agent-project/tests/test_agent_project.py | 296 +++++++++++++++++++++
```

**`agent_project` 配下は 1 行も変更していない。** テストの 296 行はすべて追加で、削除は 0 行（`git diff --numstat` = `296 0`）。

### 1.1 intake 境界 — 調整は不要だった

t2 契約 §3 の id / title 型正規化は t5 が `model.py` へ入れ済み（HEAD `11ef6b21`）。現物を読み直した結果、intake 側にこのタスクで直す境界は残っていない。

| 確認項目 | 現状 | 判定 |
|---|---|---|
| `_parse_intake_records` の汎用性 | title/id を正規化し、他フィールドは解釈せず素通し | 維持 |
| id による冪等 enqueue | `run_intake` の `existing` 集合 + `_slug_id` 突合 | 維持 |
| 特定検出器への依存 | module フック無し。差し込み点は `intake_cmd`（プロセス境界）のみ | 契約 §3 どおり |
| パッケージ内の `codd_gate` | `model.py` に 0 件（`codd-gate` ハイフンの例示のみ。契約 §0.2 で許可） | 合格 |

「変更が不要なら何も書き換えない」に従い、コードは触っていない。無理に手を入れると t5 の 23/23 差分検証済みの実装を壊すだけになる。

### 1.2 テストの新境界追随

| 区分 | 内容 |
|---|---|
| 新設 `TestHookResolution`（12 本） | 能力スキャン、設定明示の優先、明示失敗でスキャンへ落ちないこと、契約不足 module の棄却、能力ごとの独立解決、sibling 空 / 不在（`sys.path` を汚さないことまで）、前置フィルタ、キャッシュ（成功・`None` 両方）、素の環境での実解決、**本体がプロバイダの module 名を書いていないこと** |
| 新設 `TestDoctorWiringFindings`（7 本） | findings 素通し、注入引数の到達、無言縮退、片欠け不走行（両方向・**呼ばれていないこと**を観測）、プロバイダ例外の吸収、明示指定失敗の warn、`cmd_doctor` が壊れたプロバイダで死なないこと |
| `TestIntake` へ追加（5 本） | パーサの title/id 正規化、未知フィールドの素通し、非文字列 id の受理、空白 id の自動採番、非文字列 id の冪等 |

注入点は `_hook_provider` に一本化されているので、doctor 側は `mock.patch.object(km, "_hook_provider", ...)` だけで全経路が差し替わる。走査対象ディレクトリの差し替えは `km.__file__` の付け替え（`__init__.py` の exec 合成により断片内の `__file__` は共有名前空間の変数で、`_hook_sibling_dir()` はその 1 階層上を見る）。

`test_package_does_not_name_sibling_providers` は契約 §0.1 の厳格 grep を常設テストにしたもの。禁止する名前を書き下さず sibling の実ファイル名から導くので、将来別のプロバイダが増えても効く。既存の `test_every_emitted_category_is_registered_and_labelled`（`doctor.py` のソースを読んでカテゴリ表と突き合わせる）と同じ、このファイルにすでにある書き方に合わせた。

---

## 2. 検証と結果

### 2.1 旧名が残っていないこと

```
$ grep -nE '_codd_gate_wiring_module|doctor_codd_gate_findings|_codd_gate_debt_module|_wiring_module' \
    tools/agent-project/tests/test_agent_project.py
(0 件)

$ grep -nE 'CoddGate|codd_gate' tools/agent-project/tests/test_agent_project.py
4173:class TestCoddGateNoAutoWiring(unittest.TestCase):
4177:    sibling CLI（codd_gate_regression.py）でファイルへ恒久注入する。…
4187:    def test_configfile_has_no_codd_gate_auto_wiring_hook(self):
4189:        self.assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))
```

**`_codd_gate_wiring_module` を mock/patch しているテストは元から 1 件も存在しなかった**（t1 §5 の「配線経路にテスト 0 件」と整合）。タスク文が挙げる `TestCoddGateAutoWiring` も実在せず、あるのは自動配線の**不在**を固定する `TestCoddGateNoAutoWiring` だけ。したがって本タスクの実質は「mock の追随」ではなく、空だった配線経路に新境界向けのテストを新規に敷くことだった。残る 4 行の扱いは §4 に書く。

### 2.2 変異解析 — 空振りしていないことの実測

再現手順（成果物の `mutation-probe.py` / `mutation-matrix.py` が実行可能な形で入っている）:

```
python3 mutation-matrix.py     # 変異を 1 つずつ入れて全新テストを走らせ、被覆行列を出す
```

17 変異すべてを検出。新テスト 23 本すべてが最低 1 つの変異で赤くなった（全文は `mutation-matrix.txt`）。

| 変異 | 壊した性質 | 赤くなったテスト |
|---|---|---|
| M1 | フック解決そのもの（常に `None`） | HookResolution 7 本 |
| M2 | 前置フィルタ（無関係 sibling を総当たり import） | `test_unrelated_siblings_are_not_imported` |
| M3 | 明示失敗で自動検出へ落ちない | `test_explicit_name_does_not_fall_back_to_scan` ほか |
| M4 | 解決結果のキャッシュ | `test_result_is_cached_per_capability` |
| M5 | 必須属性の検査 | `test_module_without_required_attr_is_rejected` ほか |
| M6 | 能力キー名（`wiring.detect` → `detect`） | HookResolution 3 本 |
| M7 | 不在 sibling を `sys.path` へ積まない | `test_missing_sibling_dir_resolves_to_none` |
| M8 | 片側だけ解決したとき呼ばない | `test_half_resolved_provider_does_not_run` |
| M9 | プロバイダ例外の畳み込み | `test_provider_exception_degrades_to_empty` ほか |
| M10 | 明示指定ミスの warn | `test_unresolvable_explicit_provider_warns` |
| M11 | 注入引数の受け渡し（`which` を握り潰す） | `test_injected_arguments_reach_provider` |
| M12 | id の型正規化 | Intake 4 本 |
| M13 | title の正規化 | `test_parse_intake_records_normalizes_title_and_id` |
| M14 | プロバイダ固有名を本体へ書き戻す | `test_empty_sibling_dir_resolves_to_none` |
| M15 | findings を本体が解釈する（不透明性） | `test_provider_findings_pass_through` |
| M16 | 未指定の不在も所見にする（無言縮退） | `test_no_provider_degrades_to_empty` |
| M17 | 未知フィールドの素通し（本体が検出器の語彙を持つ） | `test_parse_intake_records_passes_unknown_fields_through` |

M14〜M17 は後から足した。M1〜M13 は「境界を殺す」方向の変異ばかりで、**縮退・素通し・不透明性を主張するテストは殺しても同じ返り値になって隠れる**。緩める方向（固有名を書き戻す・findings を選別する・黙るべき所で喋る・未知フィールドを落とす）でないと観測できない。この 4 本は最初の 13 変異では全部緑のままで、行列を取らなければ空振りと区別がつかなかった。

### 2.3 テスト実行

| 環境 | 対象 | 結果 |
|---|---|---|
| 先端 `38a1ccd8` + 本変更 + t4 相当の doctor | `TestHookResolution` `TestDoctorWiringFindings` `TestIntake` `TestCoddGateNoAutoWiring` | **34 tests OK** |
| 同上 | 全体 | **738 tests / failures=3**（契約 §5 が合格条件とする main 由来の 3 件のみ） |
| 先端 `38a1ccd8` + 本変更（**t4 未着**） | 同 4 クラス | **34 tests / failures=8**（§3 に詳述） |
| 作業ツリー `11ef6b21` | 受入 3 件（`TestIntake.test_run_intake_enqueues_and_dedups_by_id` / `TestLoopEngineering.test_regression_gate_{blocks_on_failure,passes}`） | OK (rc=0) |
| 作業ツリー `11ef6b21` | `TestIntake` `TestCoddGateNoAutoWiring` | 15 tests OK |
| 作業ツリー `11ef6b21` | 受入 grep（backlog 逐語） | 0 件（rc=1 = 合格） |

作業ツリー単体では `hooks.py`（t3・`38a1ccd8`）が未取得のため `TestHookResolution` / `TestDoctorWiringFindings` は走らない。合流後の状態を測るため、ブランチ先端を provision して scratch へ複製し、そこへ本変更を載せて実行した。

---

## 3. t4（doctor）へ渡す前提 — 現時点で 7 件が赤

`TestDoctorWiringFindings` は t2 契約 §4 の doctor を前提に書いてある。ブランチ先端の `doctor.py` はまだ `_wiring_module()`（固有名 `codd_gate_wiring` 直書き）なので、`_hook_provider` を patch しても効かず 7 件とも赤い。t4 が §4 を実装すれば緑になることは、契約どおりの doctor を scratch に当てて実測済み（§2.3 の 1 行目）。**その実装を `doctor-reference-for-t4.diff` として同梱した。**

差分のうち、契約本文からは読み取りにくい 1 点を明示する。

> `_hook_misconfig_findings` は **`hooks.<系統>` キーごとに 1 件へまとめること。** `wiring.detect` と `wiring.findings` は同じ `hooks.wiring` 設定から引くので、能力ごとに素直に finding を作ると 1 つの設定ミスで 2 件出る。契約 §5 の期待は「warn が 1 件」なので、前半キーで dedupe する。

---

## 4. 前提・未解決・範囲外

**採用した前提**

1. **`TestCoddGateNoAutoWiring` は改名しない。** 契約 §5 は改名を任意とし、契約 §6 の完了判定コマンドがこのクラス名を逐語で叩く。改名すると gate/verify がテスト名解決に失敗する。テストファイルは契約 §0.1 の厳格 grep（`-- tools/agent-project/agent_project`）の対象外なので、残しても完了判定に影響しない。タスク文の「旧名が残っていないこと」は、**mock/patch していた注入点の旧名**（`_codd_gate_wiring_module` / `_wiring_module` / `doctor_codd_gate_findings`）と読み、そちらは 0 件を確認した。
2. `hasattr(km, "_apply_codd_gate_auto_wiring")` のアサート文字列は残す（契約 §5 の明示指示。禁止する対象の名前そのもの）。
3. intake は「調整が不要と確認できたら触らない」を調整の一形態と読んだ。t5 の実装が契約 §3 を満たしているため、重ねて書き換えない。

**未解決**

- `TestDoctorWiringFindings` 7 件は t4 が着地するまで赤い。fan-out の並列実行では避けられない（契約 §5 も「実装前に書けば赤になるのが正しい」と明記）。gate は t3+t4+t5+t6 の合流後に判定すること。
- 変異解析は scratch 複製上で実施（共有チェックアウトを汚さないため）。CI へ常設する場合は別途タスク化が要る。

**範囲外で見つけたこと（手を出していない）**

- `@followup` 変異解析（`mutation-probe.py`）を回帰の定常ゲートへ組み込む。今回は 1 回限りの実測に留めた。
- `@followup` 契約 §6 の完了判定コマンドが `TestCoddGateNoAutoWiring` を逐語で参照している。クラス名から `CoddGate` を落とすなら、契約側の記述と同時に直す必要がある（テスト単独で改名すると gate が壊れる）。
- 既存 3 failures（`TestDaemonRouting.test_kf_base_passes_flow_config` / `TestJournalRotation.test_rotation_archives_and_starts_fresh` / `TestProjectLayer.test_version_inherits_master_charter`）は main 由来。t1 が切り分け済みで、契約 §5 が「直そうとしないこと」としているため触っていない。
