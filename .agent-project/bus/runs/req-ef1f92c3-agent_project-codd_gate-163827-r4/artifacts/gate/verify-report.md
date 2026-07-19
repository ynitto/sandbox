# t3〜t6 敵対的検証レポート（gate / r4）

判定: **fail**（重大 2 件 / minor 2 件）。t3〜t6 の実装は t2 契約に忠実で、契約違反は無い。
落ちたのは「契約が固定した振る舞い」と「テストが守れていない不変条件」の 2 点。

対象: `ap/agent_project-codd_gate-163827` @ `da915dc8`。基準点 main = `9a7302ff`（merge-base）。
プローブは全て `artifacts/gate/` に同梱（`trace_probe.py` / `e2e_probe.py` / `intake_probe.py` /
`intake_e2e.py` / `mutations.py`）。

---

## 検証項目ごとの合否

| # | 項目 | 判定 |
|---|---|---|
| 1 | 名前だけ消えて配線が死んでいないか（実行トレース） | **pass** |
| 2 | テストが空振りしていないか（ミューテーション） | **fail**（12/16 kill、致命的な生存 1 件） |
| 3 | codd_gate 連携の e2e が従前どおり成立するか | **fail** |
| 4 | intake の id 冪等が維持されているか | **pass** |
| 5 | プロバイダ不在環境で例外を出さないか | **pass** |
| — | スコープ | pass（差分は `tools/agent-project` 配下のみ） |
| — | 完了判定 grep 1・2 | pass（ともに 0 hit） |
| — | 全体スイート | pass（733 tests / failures=3。契約 §5 の main 由来 3 件と一致） |

---

## (1) 配線は生きている — pass

`_hook_import` を traced ラッパへ差し替えて実行トレースを取った。名前が消えただけで死んでいる、
という疑いは晴れた。

```
$ PYTHONPATH=. python3 artifacts/gate/trace_probe.py
"import_trace": [
  {"import_attempt": "codd_gate_wiring", "required": ["detect_wiring"],   "ok": true},
  {"import_attempt": "codd_gate_wiring", "required": ["doctor_findings"], "ok": true}
]
"resolved_detect": "codd_gate_wiring", "resolved_findings": "codd_gate_wiring"
"detect_is_real_module": true, "findings_is_real_module": true, "findings_count": 2
```

`hooks` 未指定の素の環境で sibling 能力スキャンが `codd_gate_wiring` を実際に import し、
`doctor_wiring_findings` が所見 2 件を返している。差し込み点は動いている。

## (5) 縮退は無言・無例外 — pass

sibling の `codd_gate_*.py` を 1 本も置かない複製ツリーで実測。

```
hooks= {} regression_cmd= None intake_cmd= None
detect= None
findings= []
OK: no exception
$ python3 agent-project.py doctor --root <proj> --json   → 完走・"unresolved": 8
```

`codd_gate_regression` は `agent_project` から一切参照されない（厳格 grep 0 hit。回帰経路は
`regression_cmd` のプロセス境界のみ）ので、不在は自明に無害。

## (4) intake の id 冪等 — pass

変更前の正 = 未変更で残る sibling `codd_gate_debt.parse_debt_output`。11 ケース（int / 0 / float /
空文字 / 空白のみ / None / 欠落 / 前後空白 / title 空 / 非 object / 追加フィールド）で突き合わせ。

```
"MATCH_specs": true,
"MATCH_errors": true
```

`run_intake` の実 e2e も main と一致（タイムスタンプ差のみ）:

| | run1 | run2 | backlog |
|---|---|---|---|
| main | 4 件 `["0","123","C-<ts>","x"]` | 1 件 | 5 |
| 本ブランチ | 4 件 `["0","123","C-<ts>","x"]` | 1 件 | 5 |

id を持つレコードは冪等。id 無しレコードが毎回増えるのは冪等キーが無いため（main と同一挙動）。

---

## fail-1 : e2e 連携が main から切れている（検証項目 3）

**どこで**: `agent_project/configfile.py:361`（`build_config` の `return cfg`。main では直前に
`_apply_codd_gate_auto_wiring(cfg)` があった）

**何が**: repos.json を持つプロジェクトで、main は config 読み込み時に `regression_cmd` /
`intake_cmd` を自動配線していた。本ブランチは両方 `None` のまま。同一プローブの実測:

```
                     main(9a7302ff)                          本ブランチ(da915dc8)
A_regression_cmd     "codd-gate verify --base ... --repos"    null
A_intake_cmd         "codd-gate tasks --debt --repos ..."     null
B_findings           []（結線済みなので所見なし）              2 件「…が未結線」(info)
D_gate_cmd_present   true                                    false
```

**影響**: 既存プロジェクトを本版へ上げると、`regression_cmd` が消えて**グローバル回帰検査
（巻き込み事故の検知）が黙って走らなくなる**。`intake_cmd` も消えるので model debt の汲み上げが
止まる。つまり「configfile 配線 → doctor findings → model debt → regression gate」の鎖は
**従前どおり成立しない**。doctor が `fix:` に設定文言を出すので気づける導線はあるが、severity は
`info` で、既存利用者に対する移行手順・リリースノートは差分に無い。

**責任の所在**: これは t3〜t6 の逸脱ではない。削除は r0（`b694cb91`）で行われ、t2 契約 §2「守ること」
が「自動配線を復活させない。`build_config` は `regression_cmd`/`intake_cmd` を補わない」と明記し、
r0 が新設した `TestCoddGateNoAutoWiring`（`tests/test_agent_project.py:3928`。**main には存在しない**）
が復活を禁じている。t3 も完了報告で「main 基準では未達。gate/synth の判断材料として明示する」と
申し送っていた。

**差し戻し先と直し方**: 実装ノードではなく**設計・契約の所有者（t2 / synth）**へ。二択で明示決定が要る。

- (a) 破壊的変更として受け入れる → 移行ノート（`tools/agent-project/README.md`）に「本版から
  `regression_cmd` / `intake_cmd` は自動配線されない。doctor の指示に従って
  `agent-project.yaml` へ明示すること」を書き、doctor の当該 2 所見を `info` → `warn` へ上げて
  気づけるようにする。
- (b) 等価を維持する → `_hook_provider("wiring.detect", cfg)` 経由で `build_config` の自動配線を
  固有名なしに復活させる（`codd_gate` の文字列は増えないので grep 条件は保てる）。この場合
  `TestCoddGateNoAutoWiring` の 2 ケースは意図的に赤くなるので、契約 §2 とセットで改訂する。

## fail-2 : `id: 0` の冪等ガードにテストが 1 件も無い（検証項目 2）

**どこで**: ガード = `agent_project/model.py:531`、テスト = `tests/test_agent_project.py:301-312`

**何が**: t5 が「素直に書くと再発する」と判断してコメント付きで残したガード

```python
rid = raw.get("id")
if rid not in (None, ""):   # `0` は「id が無い」ではないので or "" で潰さない
```

を、素直な形 `rid = str(raw.get("id", "") or "").strip()` へ退行させても、**TestIntake 11 件が
全部緑のまま通る**。パーサのテスト（:304-311）が使う id は `123` / `"  x  "` / `"   "` だけで、
`0` が 1 件も無い。

**実測（退行版）**:

```
$ PYTHONPATH=. python3 tests/test_agent_project.py TestIntake
Ran 11 tests in 0.624s
OK                                        ← テストは気づかない

$ 実挙動  入力 [{"title":"D","id":0}] を 3 回 intake
parse = [{'title': 'D'}]                  ← id が消える
run1=['D-153902'] run2=['D-153902-2'] run3=['D-153902-3']
backlog=['D-153902','D-153902-2','D-153902-3']  IDEMPOTENT=False
```

現行コードは正しい（`run1=['0'] run2=[] run3=[]`）。壊れているのは**守り**で、`id: 0` を返す
検出器がいる限り、intake のたびにタスクが無限に増殖する退行を誰も止められない。検証項目 4 の
冪等そのものが無防備な状態。

**直し方**: `tests/test_agent_project.py:304` の入力配列へ `{"title": "D", "id": 0}` を足し、
期待値へ `{"title": "D", "id": "0"}` を加える。加えて `TestIntake` に `id: 0` の run_intake 2 回で
2 回目 0 件を見る冪等ケースを 1 本足す（既存 `test_run_intake_dedups_by_id_after_normalization`
と同型で入力を `0` にするだけ）。

---

## minor

- **(minor) `test_broken_hooks_type_is_reported` が分岐を識別できない** —
  `tests/test_agent_project.py:4128`。`doctor.py:300` の型不正分岐を `if False:` にしても緑のまま。
  `_hook_resolution_error`（`hooks.py:141`）が別経路で同じ severity の warn を作るため、
  `[f["severity"] for f in got] == ["warn"]` では区別が付かない。title
  （「hooks の設定型が不正」）まで assert すれば分岐を固定できる。振る舞い自体は warn が出るので
  実害は無い。
- **(minor) 走査の決定性ガードが未テスト** — `hooks.py:84` の `sorted()` を外しても、`hooks.py:86` の
  `startswith("_") / isidentifier()` フィルタを外しても、スイートは緑（ミューテーション 6・7 が生存）。
  現状は契約を満たす sibling が 1 本だけなので表面化しないが、契約 §2.4 が明示的に要求している
  性質なので、複数候補を置いた一時ディレクトリで採用順を見るケースがあると守れる。

---

## ミューテーション結果（全 16 件・独立に作成）

ベースライン 4 スイートは全て緑。作業ツリーは触らず複製ツリー上で実施。

| # | 壊した性質 | 結果 |
|---|---|---|
| 1 | 明示指定の解決失敗で自動検出へ落ちる | KILLED |
| 2 | キャッシュしない | KILLED |
| 3 | 前置フィルタ撤去 | KILLED |
| 4 | 必須属性チェック撤去 | KILLED |
| 5 | 前半キー（hooks.wiring）を引かない | KILLED |
| 6 | `sorted()` 撤去 | **SURVIVED**（minor） |
| 7 | `_`始まり/非識別子の除外を撤去 | **SURVIVED**（minor） |
| 8 | 片方だけ解決でもプロバイダを呼ぶ | KILLED |
| 9 | プロバイダ例外を畳まない | KILLED |
| 10 | 設定ミス warn を出さない | KILLED |
| 11 | hooks 型不正の warn を出さない | **SURVIVED**（minor） |
| 12 | `id: 0` を潰す素直な書き方へ退行 | **SURVIVED**（fail-2） |
| 13 | id を strip しない | KILLED |
| 14 | id を文字列化しない | KILLED |
| 15 | title を strip/文字列化しない | KILLED |
| 16 | 自動配線を復活させる | KILLED |

12 KILLED / 4 SURVIVED。8・9 が KILLED なのは t6 が初回に緑だった 2 件を実測で見つけて
アサートを格上げした結果で、その修正は有効に効いている。
