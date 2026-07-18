# agent_project × codd_gate 参照点インベントリ（r4 / t1）

対象ツリー: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-81332-8rja6_t1/sandbox`
HEAD: `d5e03f4`（`[agent-flow] rev (…-r0)`）／ベースライン main: `9a7302f`

## 0. 先に結論

**タスクの hints が挙げた除去/改名対象 4 シンボルは、すでに HEAD 上に存在しない。** r0 の 5 コミットが
`configfile.py` / `doctor.py` / `model.py` とテストへ適用済みで、受入 grep はこのツリーで PASS する。
r4 に残っているのは「実装作業」ではなく「取りこぼしの確認」で、実測で見つかった穴は 1 点だけ
（§5: 改名後の `doctor_wiring_findings` / `_wiring_module` がテスト無しになった）。

## 1. 受入条件の実測（backlog の verify を逐語実行）

backlog `agent_project-codd_gate-163827.md` の verify 後半:

```
! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
    -- tools/agent-project/agent_project
```

| 項目 | 実測 |
|---|---|
| ヒット行数 | **0 行** |
| `git grep` 終了コード | `1`（＝未ヒット）→ 否定形 `!` は真 |
| 判定 | **PASS** |

ヒットする全行のリスト＝**空**。これが本タスクが求める「実測リスト」の本体である。

補足（スコープを `tools/agent-project` 全体へ広げた場合）: 47 行ヒットする。内訳は
`tools/agent-project/codd_gate_*.py`（sibling プロバイダ本体、6 ファイル）とその専用テスト
`tests/test_codd_gate_*.py`（5 ファイル）＋ `tests/test_agent_project.py:3891,3893`。
**いずれも受入 grep のスコープ外**であり、sibling プロバイダが自分自身を import するのは
設計どおり（本体＝`agent_project/` に codd_gate 名を残さない、が条件）。

## 2. 参照点一覧（除去 / 改名 / 維持）

### 2.1 hints 指定の 4 シンボル — すべて処理済み

| main での位置 | シンボル | 役割 | 呼び出し元(main) | 区分 | HEAD での姿 |
|---|---|---|---|---|---|
| `configfile.py:201` | `_apply_codd_gate_auto_wiring` | build_config 時に repos.json を見て regression_cmd/intake_cmd をメモリ上へ自動注入 | `configfile.py:376`（`build_config` 内） | **除去** | 無し。有効化は yaml 明示か sibling CLI 注入へ移行 |
| `doctor.py:287` | `_codd_gate_wiring_module` | sibling `codd_gate_wiring` の遅延解決 | `doctor.py:314` | **改名** | `_wiring_module`（`doctor.py:287`） |
| `doctor.py:309` | `doctor_codd_gate_findings` | 配線所見を doctor finding 形式で返す | `doctor.py:528` | **改名** | `doctor_wiring_findings`（`doctor.py:321`、呼び出し元 `doctor.py:540`） |
| `model.py:494` | `_codd_gate_debt_module` | sibling `codd_gate_debt` を遅延解決し intake 出力を解釈 | `model.py:552`（`run_intake` 内） | **除去→汎用置換** | `_parse_intake_records`（`model.py:494`）。特定検出器に依存しない汎用パーサ |

### 2.2 `import codd_gate_*` / `from codd_gate_*` — agent_project 配下は 0 件

`agent_project/` 内に import 文は無い。唯一の名前解決は `doctor.py:301-307` の
`importlib.import_module(provider)`（`provider = "codd_gate_wiring"` は**文字列リテラル**）で、
受入 grep の 3 分岐すべてに掛からない:

- `(import|from)[[:space:]]+codd_gate` → import 文でないため不一致
- `_apply_codd_gate` → 不在
- `_codd_gate` → 直前が `"`（アンダースコアでない）ため不一致

**区分: 維持。** 差し込み点そのものであり、`required = ("detect_wiring", "doctor_findings")` の
契約チェック＋解決失敗時 `None` → 空リスト no-op 縮退で、プロバイダ欠落でも doctor は落ちない。

### 2.3 agent_project 配下に残る "codd" 文字列 — 全 11 行、すべて維持

`git grep -n -i codd -- tools/agent-project/agent_project` の実測全行:

| file:line | 種別 | 区分 |
|---|---|---|
| `charter.py:373` | docstring（repos.json を外部ツールへ渡す説明） | 維持 |
| `configfile.py:119` | 既定値辞書のコメント（intake_cmd の例示） | 維持 |
| `configfile.py:467` | `--intake-cmd` の help 文字列（例示） | 維持 |
| `doctor.py:167` | docstring（過去実障害の記録） | 維持 |
| `doctor.py:288` | `_wiring_module` docstring | 維持 |
| `doctor.py:302` | `provider = "codd_gate_wiring"` — **唯一の実コード参照** | 維持（差し込み点） |
| `doctor.py:324` | `doctor_wiring_findings` docstring | 維持 |
| `model.py:497` | `_parse_intake_records` docstring（非依存を明言） | 維持 |
| `model.py:528` | `run_intake` docstring（例示） | 維持 |
| `mr.py:465` | コメント（cwd 選択の理由） | 維持 |
| `verify.py:9` / `verify.py:356` | コメント／許可コマンド allowlist の `"codd-gate"` | 維持 |

`verify.py:356` は CLI 名（ハイフン）の allowlist エントリで、module 名ではない。受入 grep 対象外。

## 3. intake の JSON パース＋id 冪等処理（現在の実装位置）

| 位置 | 内容 |
|---|---|
| `model.py:494-522` | `_parse_intake_records(text)` — `enqueue --json` 同形式（object 1 件／配列）を spec dict 列へ正規化。レコード単位検証で、非 object・title 欠落はそのレコードだけ errors 送りにして残りは通す。`model.py:519` が `task.schema.json` の required を明示参照 |
| `model.py:525-577` | `run_intake(cfg)` — intake_cmd 実行 → `_parse_intake_records` → 冪等取り込み |
| `model.py:562` | 冪等の実体: `existing = {f.stem for f in cfg.backlog.glob("*.md")}` |
| `model.py:564-566` | `sid = _slug_id(sp.get("id"))` が `existing` にあれば skip（現役 backlog 重複の再投入抑止） |
| `model.py:156` | `_slug_id` — id 正規化 |
| `model.py:180-182` | 明示 id を**冪等キー**として扱い改名しない旨（`_unique_task_id`） |
| `model.py:416,432,448-462,486` | `run_inbox` 側の冪等（archive 済み id の再投入を skip） |
| `model.py:491` | `_INTAKE_LAST` — プロジェクト別スロットリング |

**区分: 全て維持。** hints の「intake は schemas/task 相当の汎用 JSON パース＋id 冪等のまま維持」を
満たしており、`_codd_gate_debt_module` 経由の検出器固有解釈は `_parse_intake_records` へ置換済み。

## 4. テスト側の追随状況

| 位置 | main での姿 | HEAD での姿 | 区分 |
|---|---|---|---|
| `tests/test_agent_project.py:3877` | `class TestCoddGateAutoWiring` | `class TestCoddGateNoAutoWiring` | 改名（意味も反転: 「自動配線しない」の回帰ガードへ） |
| 同 `:3894, 3923, 3933` | `mock.patch.object(km, "_codd_gate_wiring_module")` × 3 | **削除済み** | 除去 |
| 同 `:3891-3893` | — | `test_configfile_has_no_codd_gate_auto_wiring_hook` が `assertFalse(hasattr(km, "_apply_codd_gate_auto_wiring"))` で再導入を禁止 | 新規（設計ガード） |
| `tests/test_agent_project.py:248` | `class TestIntake` | 同左。`test_run_intake_enqueues_and_dedups_by_id` / `_interval_throttles` / `_tolerates_failures` / `_one_bad_record_does_not_block_the_rest`（`:289` に「codd-gate tasks --debt 想定」コメント） | 維持 |
| `tests/test_codd_gate_*.py`（5 ファイル） | sibling プロバイダ専用テスト | 変更なし | 維持（受入スコープ外） |

## 5. 実測で見つかった唯一の穴

**`doctor_wiring_findings` と `_wiring_module` はテストが 1 件も無い。**

```
$ grep -rnE "doctor_wiring_findings|_wiring_module" tools/agent-project/tests/
（ヒット 0 件）
```

main では旧名 `_codd_gate_wiring_module` を mock する 3 テストがこの経路を押さえていたが、r0 が
`TestCoddGateAutoWiring` を `TestCoddGateNoAutoWiring` へ書き換えた際に mock 3 件を削除し、
改名後の名前で貼り直していない。結果として `doctor.py:287-333` の
「プロバイダ解決 → 契約チェック → no-op 縮退」分岐が無防備になった。受入 grep も verify テストも
これを検知しないため、r4 で潰すならここが唯一の実作業。

区分は**「改名（済）＋テスト追随（未）」**。修正は t1 のスコープ外（本タスクは調査）なので実施していない。

## 6. 検証内容と結果

| 検証 | コマンド | 結果 |
|---|---|---|
| 受入 grep | 上記 §1 の逐語コマンド | **PASS**（0 hit / exit 1） |
| 受入テスト 3 件 | `PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestIntake.test_run_intake_enqueues_and_dedups_by_id TestLoopEngineering.test_regression_gate_blocks_on_failure TestLoopEngineering.test_regression_gate_passes` | **OK**（Ran 3 tests, 0.305s） |
| 全体スイート | 同ファイル全実行 | Ran 714 tests / 289.4s / **FAILED (failures=3)** |

失敗した 3 件は `TestDaemonRouting.test_kf_base_passes_flow_config` /
`TestJournalRotation.test_rotation_archives_and_starts_fresh` /
`TestProjectLayer.test_version_inherits_master_charter`。

**本タスクと無関係な既存不具合であることを実測で確認済み**: main（`9a7302f`）を別 worktree へ
provision して同じ 3 件を実行し、**同一の 3 failures を再現**（`AssertionError: '標準ライブラリのみ'
not found in ['追加の制約']` まで一致）。worktree は release 済み。

ファイル変更は行っていない（調査タスクのため）。

## 7. 採用した前提

1. **受入 grep のスコープは `tools/agent-project/agent_project` 配下に限る**（backlog の verify 逐語）。
   sibling `tools/agent-project/codd_gate_*.py` とその専用テストのヒットは条件違反にあたらない。
   タスク文が「`import codd_gate_*` を file:line で特定」と言うため §2.2 に広域結果も併記した。
2. **`doctor.py:302` の文字列リテラル `"codd_gate_wiring"` は残してよい**。受入 grep の 3 分岐すべてに
   構文的に掛からず（§2.2 に根拠）、hints の「自動配線は sibling か設定明示に寄せ、パッケージ内に
   codd_gate 名を残さない」は import 依存を指すと解釈した。ここを消すと差し込み先の指定手段が
   失われ、`importlib` による疎結合という設計意図そのものが壊れる。
3. **HEAD が main 分岐直後ではなく r0 適用済みである点をそのまま前提とした**。作業ブランチ
   `ap/agent_project-codd_gate-163827` は detached HEAD 状態（`d5e03f4`）で、r0 の 5 コミットを含む。

## 8. 未解決事項・範囲外で見つけた問題

- **[要対応・r4 の実作業候補]** §5 のテスト欠落。`doctor_wiring_findings` にプロバイダ有／無／契約
  不一致の 3 ケースを新名で貼り直すのが最小の穴埋め。
- **[範囲外]** 全体スイートの既存 3 failures は main 由来。本 backlog の out_of_scope には該当
  しないが本タスクの原因ではないため未修正。別タスク化の要否は評価役の判断に委ねる。
  `@followup agent_project の既存テスト失敗 3 件（TestDaemonRouting/TestJournalRotation/TestProjectLayer）を切り分ける :: PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py`
- **[範囲外]** backlog の `needs_reason` にある codd-gate verify 失敗（「スキャン可能な repo がありません」）は
  repos.json 解決の環境問題で、agent_project のコードとは独立。
