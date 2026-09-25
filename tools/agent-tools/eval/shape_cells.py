"""本番の問いの形（filter / route / assess）の較正セルを増やす素材。readout_eval が読む。

2026-09-20〜21 の実測は用途ごとに 1〜7 セルしかなく、report が常に insufficient_data だった。
ここは**同じ問いの形・同じ checker** のまま入力だけを足す:

- FL: filter（候補 1 件 = boolean 1 問）。判定は judge_eval の `check_id_set`。
  正解は候補の行に書いた属性（テスト: pass 等）から従う。採用 0 件・全件も置く。
- RO: route（書込先の choice + other）。判定は project_eval の `check_route`。
  候補は project_eval の `WORKSPACES` のまま。どれにも属さない仕事は ""（other）。
- AS: assess（r の score 1 問）。判定は project_eval の `check_assess`（r だけ）。
  既存 AS1〜AS8 に無い r=2（利用者に見える機能）を足す。

正解は checker に総当たりをかけて一意に決まる割り当て（readout_eval の `oracle`）で、
LLM に付けさせない。project_eval は agent_project が読めない木で SystemExit するので、
ここでは import せず、check とタスクの生成を呼ばれたときに解く。
"""
from __future__ import annotations

import importlib


def _project_eval():
    return importlib.import_module("project_eval")


# --------------------------------------------------------------------------- filter
def _filter(goal: str, lines: "list[tuple[str, bool]]") -> dict:
    """lines は (候補の説明, 基準を満たすか)。id は c1.. の順に振る。"""
    ids = [f"c{i}" for i in range(1, len(lines) + 1)]
    want = {i for i, (_, keep) in zip(ids, lines) if keep}
    output = "\n".join(f"[{i}] {text}" for i, (text, _) in zip(ids, lines))
    return dict(kind="filter", expect=",".join(sorted(want)) or "（無し）", goal=goal,
                deps={"gen": {"output": output}},
                check=lambda d: importlib.import_module("judge_eval").check_id_set(d, want))


_PASSING = "候補のうち**テストが通っているものだけ**を残す。"
_FILTERS = {
    "FL2": _filter(_PASSING, [("通知処理の案 A。テスト: pass（8 件）", True),
                              ("通知処理の案 B。テスト: fail（2 件が失敗）", False),
                              ("通知処理の案 C。テスト: 未実行", False),
                              ("通知処理の案 D。テスト: pass（8 件）", True)]),
    "FL3": _filter("候補のうち**追加の依存を必要としないもの**（標準ライブラリのみ）を残す。",
                   [("取得処理の案 A: 標準ライブラリのみで 40 行", True),
                    ("取得処理の案 B: requests を追加して 22 行", False),
                    ("取得処理の案 C: 標準ライブラリのみで 55 行", True),
                    ("取得処理の案 D: httpx を追加して 25 行", False)]),
    "FL4": _filter("候補のうち**50 行以下のもの**を残す。",
                   [("変換処理の案 A: 30 行", True), ("変換処理の案 B: 72 行", False),
                    ("変換処理の案 C: 50 行", True), ("変換処理の案 D: 51 行", False)]),
    "FL5": _filter("候補のうち**Windows で動作を確認したもの**を残す。",
                   [("監視スクリプト案 A: /proc を読む。Linux でのみ確認", False),
                    ("監視スクリプト案 B: Windows・macOS・Linux で確認済み", True),
                    ("監視スクリプト案 C: Windows と macOS で確認済み", True),
                    ("監視スクリプト案 D: bash で書いた。Linux でのみ確認", False)]),
    "FL6": _filter("候補のうち**レビューで承認されたもの**を残す。",
                   [("設定読み込みの修正 A。レビュー: 承認", True),
                    ("設定読み込みの修正 B。レビュー: 差し戻し", False),
                    ("設定読み込みの修正 C。レビュー: 未依頼", False),
                    ("設定読み込みの修正 D。レビュー: 承認", True)]),
    "FL7": _filter("候補のうち**テストが通っていて、かつ追加の依存が無いもの**を残す。",
                   [("集計の案 A: 標準ライブラリのみ。テスト: pass", True),
                    ("集計の案 B: pandas を追加。テスト: pass", False),
                    ("集計の案 C: 標準ライブラリのみ。テスト: fail", False),
                    ("集計の案 D: 標準ライブラリのみ。テスト: pass", True)]),
    # 採用 0 件。全部落とせるか（1 つは残したくなる癖を見る）。
    "FL8": _filter(_PASSING, [("出力処理の案 A。テスト: fail（1 件が失敗）", False),
                              ("出力処理の案 B。テスト: 未実行", False),
                              ("出力処理の案 C。テスト: fail（実行時エラー）", False),
                              ("出力処理の案 D。テスト: 未実行", False)]),
    "FL9": _filter("候補のうち**Python 3.9 で動くもの**を残す。",
                   [("解析の案 A: Python 3.8 以上で動く", True),
                    ("解析の案 B: match 文を使うので Python 3.10 以上が必要", False),
                    ("解析の案 C: Python 3.9 以上で動く", True),
                    ("解析の案 D: Python 3.12 以上が必要", False)]),
    # 採用が全件。
    "FL10": _filter("候補のうち**ライセンスが MIT のもの**を残す。",
                    [("描画ライブラリ A。ライセンス: MIT", True),
                     ("描画ライブラリ B。ライセンス: MIT", True),
                     ("描画ライブラリ C。ライセンス: MIT", True),
                     ("描画ライブラリ D。ライセンス: MIT", True)]),
}


# --------------------------------------------------------------------------- route
def _route(title: str, acceptance: str, want: str) -> dict:
    return dict(purpose="route", expect=want or "（空＝判断できない）",
                title=title, acceptance=acceptance,
                check=lambda t: _project_eval().check_route(t, want))


_ROUTES = {
    "RO4": _route("judge の確度を台帳へ記録する", "agentcore の test_judge.py が通る", "agent-tools"),
    "RO5": _route("実行一覧の画面に状態で絞り込むフィルタを付ける", "画面で状態ごとに絞り込める",
                  "agent-dashboard"),
    "RO6": _route("利用者向けの手引きにインストール手順を書く", "手引きに Windows の手順がある",
                  "docs-site"),
    "RO7": _route("出張の経費精算を申請する", "申請が承認される", ""),
    "RO8": _route("eval の集計スクリプトに p90 を足す", "report.json に p90 が入る", "agent-tools"),
    "RO9": _route("工程の画面でボタンが折り返さないようにする", "ボタンが 1 行に収まる",
                  "agent-dashboard"),
    "RO10": _route("新しいノート PC を発注する", "PC が届く", ""),
    "RO11": _route("手引きの FAQ にログの置き場所を追記する", "FAQ にログの置き場所の項がある",
                   "docs-site"),
}


# --------------------------------------------------------------------------- assess
def _assess(r: int, title: str, verify: str, acceptance: str, note: str) -> dict:
    def make():
        ap = _project_eval().ap
        return ap.Task(id="t30", title=title, verify=verify,
                       extra=[("acceptance", acceptance), ("note", note)])
    return dict(purpose="assess", expect=f"r={r}", make=make,
                check=lambda v: _project_eval().check_assess(v, {"r": r}))


_ASSESSES = {
    "AS9": _assess(2, "一覧画面に並べ替えボタンを足す", "npm test -- list",
                   "一覧を名前順・日付順で並べ替えられる", "ui/list.tsx の 1 ファイル"),
    "AS10": _assess(3, "問い合わせフォームの電話番号を保存時に伏せ字にする",
                    "python -m pytest -q tests/test_contact.py", "tests/test_contact.py が通る",
                    "forms/contact.py の保存処理だけを変える"),
    "AS11": _assess(1, "テスト関数の命名を揃える", "python -m pytest -q tests",
                    "tests が通る", "tests/test_render.py の関数名だけを変える"),
    "AS12": _assess(2, "利用者がダウンロードする CSV に更新日時の列を足す",
                    "python -m pytest -q tests/test_export.py", "tests/test_export.py が通る",
                    "export/csv.py の列定義に 1 列足す"),
    "AS13": _assess(3, "月額プランの請求日を月末へ変える",
                    "python -m pytest -q tests/test_billing.py", "tests/test_billing.py が通る",
                    "billing/plans.py の定数 1 つを変える"),
}

CASES = {**_FILTERS, **_ROUTES, **_ASSESSES}
