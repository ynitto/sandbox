# doctor 導線の記述修正と完了条件ループ（verify）

判定: **pass**（完了条件コマンド 終了コード 0・修正3ファイル）

## 完了条件の実行履歴

失敗は 0 回。修正を入れてから初回実行で 0 だった。

| 回 | コマンド | 結果 |
|---|---|---|
| 1 | `unittest discover -p 'test_codd_gate_*.py'` ＋ README の grep 2本 | **rc=0**（111 tests OK） |
| 2 | 同上（repos.json 前提の追記後に再実行） | **rc=0**（111 tests OK） |

grep の内訳も個別に確認した。肯定側 `codd_gate_regression|regression_cmd|intake_cmd` は
README:276-284 / 291 / 446-452 でヒット。否定側 `build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring`
は 0 hit（`!` が成立）。

## 依存結論の再導出

gate の主張「`hooks:` を書かないと doctor は無所見」を、報告をなぞらず自分で実測した。

```
_hook_scan_siblings(('detect_wiring',))   -> None
_hook_scan_siblings(('doctor_findings',)) -> None
```

`codd_gate_wiring.py:228-229` が契約名を `def` ではなく別名で公開しており、`hooks.py` の
前置フィルタ `^def <属性名>(` に載らない。gate の判定は正しい。

ただし gate の裏取りには**もう1段の前提が抜けていた**。`hooks:` を書いても所見が出ないケースが
あり、実機で両方を突き合わせて切り分けた。

| 条件 | doctor の「未結線」所見 |
|---|---|
| `hooks:` なし・`repos.json` なし | 0 件 |
| `hooks:` あり・`repos.json` なし | **0 件** |
| `hooks:` あり・`repos.json` あり | **2 件** |
| `hooks:` なし・`repos.json` あり | 0 件 |

原因は `judge_wiring`（`codd_gate_wiring.py:145`）の `can_recommend = status.usable and
repos_path is not None` と、`charter.py:326` の `repo_registry_path` が**実在しなければ None を
返す**こと。gate は `repos.json` の実在を「schemas 互換判定の条件」としか書いていないが、実際は
**推奨コマンドを組み立てる条件そのもの**。README にこの前提も併記した（gate の指示に対する上積み）。

CLI 側は `--repos` を自分で決められるので、この前提に縛られない（`/tmp` の空プロジェクトで
`regression_wired:false` ＋推奨2件を実測）。設定を増やさない確認手段として README に据えた。

## 修正内容（3ファイル・いずれも文章のみ）

1. `README.md:287-293` — 「結線できているかは `doctor` が見る」を書き換え。設定不要の
   `python3 codd_gate_wiring.py --config …` を先に置き、doctor 所見は `hooks:` ＋
   `wiring: codd_gate_wiring` の2行を書いたときだけ到達すること、別名公開のため自動検出に
   載らないこと、doctor 経路は `repos.json` 実在が前提であることを明記。
2. `GUIDE.md:194-198` — 手順は複製せず「`hooks:` に明示したときだけ所見が出る」を追加し、
   正本が README であることを示した。
3. `agent-project.yaml.example:185-187` — 「走査は `def <契約名>(` で前置フィルタするので、
   契約名を別名で公開するプロバイダは載らない」を追記。直下の唯一の例が自動検出されない矛盾を解消。

## スコープ

差分は `tools/agent-project/` 配下の3ファイルのみ（`git status --porcelain` で確認）。
`agent_project/` パッケージ・dashboard の差分は 0。

gate の minor 4件目（`docs/designs/codd-gate-design.md:262` のモジュール表に
`recommend_regression_cmd` を追加）は**着手していない**。当タスクの書込許可が
`tools/agent-project` 配下に限定されているため。@followup として残す。

@followup docs/designs/codd-gate-design.md:262 のモジュール表（codd_gate_wiring.py 行）へ
`recommend_regression_cmd` を追記する。CLI から実際に使われる公開名で、
`codd_gate_regression.infer_default_repos_path` と対になる。
