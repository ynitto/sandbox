# codd_gate_wiring / codd_gate_base — 境界の切り離しと公開関数の存置判断（t2）

対象: `tools/agent-project/codd_gate_wiring.py`・`codd_gate_base.py`
ブランチ: `ap/sibling-163827`／変更は `tools/agent-project` 配下のみ

## 1. 結論（後続タスクが最初に知るべきこと）

**コード上の自動配線経路は着手時点で既に 0 本だった。** t1 の調査どおり
`agent_project/` は `codd_gate_*` を 1 つも import せず、両モジュールとも `cfg` にもファイルにも
書き込まない。よって本タスクで削除すべき「配線コード」は存在せず、**残っていた結合は docstring
（モジュールが自分の責務をどう名乗るか）だけ**だった。そこを新境界の記述へ差し替えた。

変更の実体:

| ファイル | 変更 |
|---|---|
| `codd_gate_wiring.py` | module docstring 差し替え（実行コード変更なし） |
| `codd_gate_base.py` | module docstring 差し替え（実行コード変更なし） |
| `tests/test_codd_gate_base.py` | **新規**（7 tests）。存置判断を裏打ちする契約テスト |

**公開シンボル・関数シグネチャ・振る舞いは一切変えていない。**
特に `detect_wiring` / `doctor_findings` の 2 つは `agent_project/hooks.py` が能力キー
（`wiring.detect` / `wiring.findings`）で引き当てる**本体との契約名**であり、改名すると
プロバイダが解決されず所見が静かに消える（例外にはならない）。この事実を docstring に明記した。

## 2. 宙に浮く公開関数の判断

### 2.1 `codd_gate_base.resolve_base_rev()` → **存置**（明示呼び出し用 API として）

呼び出し元 0 件・テスト 0 件だった関数。**削除せず残し、単体テストを新設**した。

判断理由:

1. **「呼ばれていない」の原因が欠陥ではなく設計の帰結。** 現行の推奨文字列
   (`recommend_regression_cmd`) は `--base "$KIRO_BASE_REV"` を**シェル変数参照のまま**埋め込む。
   rev の解決はシェル／`_settle_task` の venv 注入に委ねられ、Python が rev を確定させる場面が
   構造的に存在しない。配線し忘れではないので、削除は「穴の存在ごと消す」ことになる。
2. **埋めている穴が現存する。** `git_change_baseline`（`policy.py`）が空文字を返す環境
   （非 git ワークスペース・初回コミット前）では `venv = None` となり `$KIRO_BASE_REV` が
   未定義→空文字へ展開され、codd-gate の `--base ""` が `_die` する
   （`tools/codd-gate/codd-gate.py`「差分の基準 rev がありません」を実在確認済み）。
   この失敗モードは今も再現しうる。
3. **本タスクの責務定義に収まる。** 「sibling 検出・推奨文字列の生成・yaml 冪等注入・CLI 所見」
   のうち、base rev 解決は推奨文字列／argv 組み立ての materials にあたる。パッケージへ再結合
   しない限り、sibling 側に置いたままで境界を侵さない。
4. **存置コストがほぼ 0。** stdlib のみ・54 行・I/O なし・例外を投げない純粋関数。
5. **削除は不可逆で範囲外。** 実装の手がかりが `codd_gate_{...,base,...}.py は残し` と明示。

存置に伴う手当て（**API と名乗る以上テストで契約を固定する**）:
`tests/test_codd_gate_base.py` を新設し、優先順位（`KIRO_BASE_REV` → task の base ブランチ →
`HEAD~1`）・空白のみの値が次段へ落ちること・strip・`env` 省略時に `os.environ` を読むことを固定。

### 2.2 `codd_gate_wiring` の公開関数 → **全て存置**（宙に浮くものは無かった）

`regression_wired` / `intake_wired` / `recommend_regression_cmd` / `recommend_intake_cmd` /
`judge_wiring` / `WiringJudgment` はすべて `detect_wiring`・`doctor_findings` から到達可能、
かつ `tests/test_codd_gate_wiring.py`（27 tests）が直接カバー。判断の必要なし。

## 3. 検証

| 検証 | 結果 |
|---|---|
| `test_codd_gate_*.py` 一式 | **104 tests OK**（既存 97 + 新規 7） |
| `TestCoddGateNoAutoWiring`（再導入禁止の回帰ガード） | **4 tests OK** |
| `py_compile` 3ファイル | OK |
| hooks 能力解決の実地確認 | `wiring.detect` / `wiring.findings` とも `codd_gate_wiring` に解決 |
| 検出済み・未結線 → 推奨文字列と info 所見 | 出る |
| 完全結線済み → 所見 | 0 件（黙る） |
| `detect_wiring` 呼び出し前後の `cfg` | 不変（どこにも書き込まない） |
| `grep -rn "codd_gate" agent_project/` | **ヒット 0**（パッケージ→sibling の静的 import 無し） |

**t1 の「81 tests」は誤り。** 同じコマンドで実測すると新規追加前で 97 tests
（base 7 / debt 10 / detect 23 / regression 28 / routing 9 / wiring 27 = 104）。
後続が件数で回帰を判断する場合はこちらを基準にすること。

## 4. 採用した前提

- **「切り離し」を「コードを消す作業」ではなく「責務の宣言を新境界に一致させる作業」と解した。**
  配線コードが既に無い以上、モジュールが自分を「a2 相当の glue」「b3 が使う」「同一 run の
  別タスクの責務」と名乗り続けることが、読み手にとって唯一残った結合だったため。
- **docstring の書き換えはコード変更に含まれると解した。** タスクは「判断理由をコード近傍の
  コメントではなく作業報告に書く」と指示しており、これは*存置判断の理由*をコードに書くなという
  意味で、docstring 全般の凍結ではないと読んだ（責務の限定は docstring でしか表現できない）。
- **行番号参照は symbol 参照へ置換した。** `agent-project.py:4906-` 等はパッケージ分割で全滅
  （t1 §5-④）。同じ陳腐化を繰り返さないため、新記述では行番号を使わず symbol 名で参照した。
  参照先 `_settle_task`（`mr.py:494`）・`git_change_baseline`（`policy.py:219`）・
  `_task_verify_cwd`（`verify.py:122`）は実在を確認済み。

## 5. 範囲外で見つけた問題（手を出していない）

- @followup `codd_gate_detect.py:4,8` の docstring がまだ `agent-project.py:3477` と
  bus artifact パス（`run-20260712-213419-5922/artifacts/d1`）を参照。同種の陳腐化が
  `codd_gate_status` / `routing` / `debt` / `regression` にも残る（a1/a4/b2/d1/d2 の run 内符牒）。
  本タスクの対象 2 ファイル外のため未修正。
- @followup `docs/designs/codd-gate-design.md:284-304` が消滅した `_apply_codd_gate_auto_wiring`
  を現存機能として記述（t1 §5-① と同じ）。**本タスクの docstring 差し替えで実装側の記述は
  新境界に揃ったので、設計書だけが取り残された状態**になった。
- @followup README:281-283 の「repos.json が実在する環境で検出」は旧発火条件（t1 §5-③）。
  `detect_wiring` は repos.json の有無に関わらず走り、実在時に schema チェックが増えるだけ
  ——本タスクで実地確認済み（`repos_path=None` でも判定は成立する）。
- @followup `codd_gate_debt.py` も本番経路の呼び出し元 0 件。docstring 自身が意図的存置と
  明記しており、`resolve_base_rev` と同じ整理（明示呼び出し用 API + テスト）が馴染むが、
  対象 2 ファイル外のため触っていない。
- @followup `agent_project/hooks.py` の `_HOOK_CACHE` は `None` もキャッシュする。
  cfg を差し替えるテストを書く後続は `_HOOK_CACHE.clear()` が必要。
