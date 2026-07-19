# configfile の配線等価性（r4 / t3）

対象は t2 契約 §2（configfile 実装者）。判定に使ったのは
`configfile_wiring_probe.py` を 3 本のツリーで走らせた `wiring.{main,head,now}.json`。

## 測ったもの

t3 の完了条件「どのモジュールがどの順で配線されるか、未存在時に何が起きるか」を 4 つの観測へ分解した。

| 観測 | 意味 |
|---|---|
| `build_config.*.regression_cmd` / `intake_cmd` | 設定読み込みが配線する値 |
| `provider_imported_by_config_load` | 設定読み込みが provider を import するか（副作用の有無） |
| `sibling_candidates_in_scan_order` / `capability_resolution` | 候補の順序と採用 module |
| `absent_provider_scan` | provider 不在時に例外か無言縮退か |

比較したツリー: `main`（分岐元）／`head`＝`11ef6b21`（r0 適用済み・r4 未適用）／`now`（作業ツリー）。
`main` と `head` は `git_worktree.py provision` で取得した未変更のチェックアウト。

## 結果

### head → now（＝ t3 が入れた変更ぶん）: 配線結果は完全一致

```
                       regression_cmd / intake_cmd            provider import
bare                   None            / None                 []
explicit_commands      'my-regression' / 'my-intake'          []
repos_json_present     None            / None                 []
hooks_configured       None            / None                 []
hooks_bogus            None            / None                 []
```

5 シナリオすべてで head と now が一致する。`diff wiring.head.json wiring.now.json` の差分は
**追加された観測項目だけ**で、配線値・import 副作用の行は 1 行も動いていない:

- `cfg.hooks` が `<no attr>` → 実値（新設フィールド）
- `capability_resolution` が `{}` → `wiring.detect` / `wiring.findings` とも `codd_gate_wiring`
- `absent_provider_scan` が `<no scan hook>` → 両能力とも `null`（例外なし）

つまり `_hook_provider` は**設定読み込み経路からは誰も呼んでいない**追加の差し込み点であり、
build_config の振る舞いには触れていない。等価性はここで成立している。

### main → head: 一致しない（r0 の意図的な仕様変更。t3 由来ではない）

`main` は repos.json が実在すると config 読み込み時に自動配線していた。

```
repos_json_present  reg='codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json'
                    intake='codd-gate tasks --debt --repos ./repos.json'
                    import=['codd_gate_detect','codd_gate_routing','codd_gate_status','codd_gate_wiring']
```

`head`（r0 適用済み・r4 着手前）では同じシナリオが `None` / `None` / `[]`。
`_apply_codd_gate_auto_wiring` は **r0 の時点ですでに削除済み**で、`git diff main HEAD` にその差分が入る。
t2 契約 §2「守ること」（自動配線を復活させない・build_config は両コマンドを補わない）と、
このブランチの `TestCoddGateNoAutoWiring`（3 ケース）がその決定を固定している。

**この差は t3 の作業では埋めない**（契約と既存テストの双方に正面から反する）。ただし
「配線結果を変更前と等価に保つ」を main 基準で読むと未達なので、gate/synth の判断材料として §未解決に残す。

## 契約 §2 の各項目の実測

| 契約 | 実測 |
|---|---|
| §2-1 既定は sibling 能力スキャン | `wiring.detect` → `codd_gate_wiring` |
| §2-2 設定明示が sibling より優先 | 明示した module が返る |
| §2-3 明示が解決不能なら自動検出へ落ちない | `None`（sibling があっても拾わない） |
| §2-4 契約不足の module は棄却 | `hooks.wiring = "json"` → `None` |
| §2-5 フルキーが前半キーに優先 | `wiring.detect` 指定が `wiring` を上書き |
| §2-6 `hooks` が dict でない | 無視して自動検出（doctor が warn を出す） |
| §2-7 前置フィルタ | 直接 import は `codd_gate_wiring` のみ（下記注） |
| §2-8 キャッシュ | 2 回呼んでスキャンは 1 回 |
| §2-9〜11 `_hook_resolution_error` | 未指定 `None` / 明示ミス・型不正で理由文字列 |

**§2-7 の注（t6 への申し送り）**: スキャン後の `sys.modules` 差分には
`codd_gate_detect` / `codd_gate_routing` / `codd_gate_status` が現れる。これは前置フィルタの漏れではなく、
採用された `codd_gate_wiring` 自身の import（`codd_gate_wiring.py:40,47,48`）。
直接 import されないことが確認できるのは `codd_gate_base` / `codd_gate_debt` / `codd_gate_regression` の 3 本。
契約 §5 のテストケース 6 を「差分に契約を満たさない sibling が現れない」と字義どおり書くと**誤って赤になる**ので、
この 3 本の不在を見る形にすること。なお main も config 読み込み時に同じ 4 本を import していた（推移的依存の範囲は不変）。

## 再現手順

```
python3 ~/.kiro/skills/flow-worker/scripts/git_worktree.py provision <repo> --ref main       # -> $MAIN
python3 ~/.kiro/skills/flow-worker/scripts/git_worktree.py provision <repo> --ref 11ef6b21   # -> $HEAD
for R in $MAIN $HEAD $NOW; do
  PYTHONPATH=$R/tools/agent-project python3 configfile_wiring_probe.py
done
diff wiring.head.json wiring.now.json    # 配線値・import 行に差分なしが合格
```

## テスト・grep

```
PYTHONPATH=. python3 tests/test_agent_project.py \
    TestCoddGateNoAutoWiring TestConfigFile \
    TestIntake.test_run_intake_enqueues_and_dedups_by_id \
    TestLoopEngineering.test_regression_gate_blocks_on_failure \
    TestLoopEngineering.test_regression_gate_passes
  -> Ran 18 tests / OK

PYTHONPATH=. python3 tests/test_agent_project.py
  -> Ran 714 tests / FAILED (failures=3)
     TestDaemonRouting.test_kf_base_passes_flow_config
     TestJournalRotation.test_rotation_archives_and_starts_fresh
     TestProjectLayer.test_version_inherits_master_charter
     いずれも main 由来（t1 が切り分け済み・契約 §5 が合格条件として明記）

git grep -nE 'codd_gate' -- configfile.py hooks.py config.py __init__.py   -> 0 件
git grep -nE '(import|from) codd_gate|_apply_codd_gate|_codd_gate' -- agent_project  -> 0 件
git grep -nE 'codd_gate' -- agent_project  -> doctor.py:288,302,324 が残存（t4 スコープ）
```
