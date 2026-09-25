"""ステートマシンの判定 2 面（遷移条件・検査失敗）の素材と正解。

`readout_eval.py` がここから入力と正解を借りる。judge_eval / project_eval と同じ作法で、
**正解は入力を作る規則から従う**（構成的なラベル。人の判断を混ぜない）:

- 遷移条件（`_sm_judge_conditions`）: 出力に**その条件を満たすことを示す行があるか**で決まる。
- 検査失敗（`_sm_check_triage`）: 直し先が**作業物の中にあるか（fixable）／外にあるか**で決まる。

検査失敗の素材は、本番の決定的な段（`_SM_ENVIRONMENT_PATTERNS`）に**引っかからない**失敗
だけにしてある。引っかかる失敗は judge まで来ない——来ない入力で judge を測ると、本番より
易しい面を測ることになる。
"""
from __future__ import annotations

# --- 遷移条件 -------------------------------------------------------------
# 条件は 2 件。出力は「片方だけ満たす」「どちらも満たさない」の 2 通りで、判定に要る事実は
# すべて出力の行として書いてある（行が無いものは満たしていない）。
_CONDITIONS = [{"index": "1", "condition": "テストが全部通った"},
               {"index": "2", "condition": "新しい依存を追加した"}]

TR1_OUTPUT = """pytest -q を実行しました。
24 passed, 0 failed（3.2s）
変更したのは tools/report/render.py の 1 ファイルだけです。
requirements.txt は触っていません。"""

TR2_OUTPUT = """pytest -q を実行しました。
3 failed, 21 passed（4.1s）
落ちたのは render の 3 件です。
requirements.txt は触っていません。"""


def check_evals(evals: dict, want: dict):
    got = {k: bool(v) for k, v in (evals or {}).items()}
    if got == want:
        return True, f"evals={got}"
    return False, f"evals={got}（期待 {want}）"


# --- 検査失敗 -------------------------------------------------------------
# 本番は決定的な段を先に通す（command not found 等）。ここに置くのはその網に掛からない失敗で、
# CT1 は直し先が作業物の外（docker が無い）、CT2 は中（表明が食い違っている）。
CT1_ARGV = ["npm", "run", "build"]
CT1_OUTPUT = """> build
> docker build -t report .

Error: Docker daemon に接続できません（docker.sock が応答しません）
ビルドを中止しました。"""

CT2_ARGV = ["python", "-m", "pytest", "-q", "tools/report"]
CT2_OUTPUT = """E   AssertionError: assert 41 == 42
E    +  where 41 = total_tokens(rows)
1 failed, 12 passed（2.8s）"""


def check_fixable(value, want: bool):
    if value is None:
        return False, "決めていない（judge が答えを返していない）"
    if bool(value) is want:
        return True, f"fixable={bool(value)}"
    return False, f"fixable={bool(value)}（期待 {want}）"


# TR3 は**沈黙**を読ませる面である。条件が指すもの（CHANGELOG）は出力のどこにも出てこない。
# evaluator の面では、要求が 3 段だと書いてあると、状態に無い段まで「ある」と答えた
# （2026-09-20）。同じ埋め方が遷移条件でも起きるか——起きるなら「書いていないこと」は
# 満たしていないと読めていない。
_CHANGELOG_CONDITION = [{"index": "1", "condition": "CHANGELOG に変更点を追記した"}]

# --- 契約の語 -------------------------------------------------------------
# 出力の第 1 行に契約の語が無いとき、本番は決定的な補修を試し、直せない分だけ judge へ
# 「どの語か」を 1 問訊く。だから素材も**決定的に直せない出力**にする（語がどこにも
# 書かれていない）。正解は「本文が何を結論しているか」から従う。
CONTRACT_RULE = "startswith:PASS,FAIL,RETRY"

CW1_OUTPUT = """検査を 3 回まわしましたが、いずれも同じ表明で落ちています。
tools/report/render.py:82 の合計が 1 ずれています。
入力を変えても再現し、再実行しても直りません。"""

CW2_OUTPUT = """作業ディレクトリの中身を一覧しました。
tools/report/ には render.py と __init__.py があります。
続きの指示を待ちます。"""


def check_contract(value, want: str):
    """本番が第 1 行へ補う語（決めなければ ""）。"""
    got = str(value or "")
    if got == want:
        return True, f"語={got or '（補わない）'}"
    return False, f"語={got or '（補わない）'}（期待 {want or '（補わない）'}）"


# --- 判定ステート ---------------------------------------------------------
# 宣言した選択肢から 1 語を選ぶステート。`other` と確度不足は unsure の語になる
# （決められないと言えることが judge を使う理由の 1 つ）。
JUDGE_STATE_SPEC = {"question": "この問い合わせは不具合の報告か、機能の要望か。",
                    "choices": {"BUG": "以前は動いていた／意図と違う、という報告",
                                "FEATURE": "今は無い機能を足してほしい、という要望"},
                    "min_confidence": 0.6}

JS1_INPUT = """請求書の合計が税込みで 1 円ずれます。
v4.1 では正しかったので v4.2 で変わったのだと思います。
再現手順は添付のとおりです。"""

JS2_INPUT = """いつもお世話になっております。
来月の請求サイクルの締め日を教えてください。
資料があれば送っていただけると助かります。"""


def check_state_choice(value, want: str):
    got = str(value or "")
    if got == want:
        return True, f"判定={got or '（決めていない）'}"
    return False, f"判定={got or '（決めていない）'}（期待 {want}）"


# --- 2026-09-25 追加（本番の問いの形のまま入力だけを増やす）---------------------------
# 遷移条件は「書いてあるが満たしていない」（未対応・未実行・作っただけ）を混ぜる。
# 検査失敗は本番の決定的な段（`_SM_ENVIRONMENT_PATTERNS`）に掛からない文面だけにする。
# 契約の語は PASS / RETRY / 無し を足す（既存は FAIL と無しの 2 件だけ）。
_TR_MORE = {
    "TR4": ([{"index": "1", "condition": "テストが全部通った"},
             {"index": "2", "condition": "lint がエラー 0 で終わった"}],
            "pytest -q を実行しました。\n30 passed, 0 failed（5.0s）\n"
            "ruff check を実行しました。\nFound 2 errors.", {"1": True, "2": False}),
    "TR5": ([{"index": "1", "condition": "PR を作成した"}],
            "変更をコミットして git push しました。\nPR はまだ作成していません。", {"1": False}),
    "TR6": ([{"index": "1", "condition": "PR を作成した"}],
            "gh pr create を実行しました。\nhttps://github.com/example/report/pull/42",
            {"1": True}),
    "TR7": ([{"index": "1", "condition": "ビルドが成功した"},
             {"index": "2", "condition": "dist/app.zip を書き出した"}],
            "npm run build を実行しました。\nBuild completed in 12s.\n"
            "dist/app.zip（2.1MB）を書き出しました。", {"1": True, "2": True}),
    "TR8": ([{"index": "1", "condition": "テストが全部通った"}],
            "render.py の修正を保存しました。\nテストはまだ実行していません。", {"1": False}),
    "TR9": ([{"index": "1", "condition": "新しい依存を追加した"},
             {"index": "2", "condition": "README を更新した"}],
            "requirements.txt に httpx==0.27 を追加しました。\nREADME.md は変更していません。",
            {"1": True, "2": False}),
    "TR10": ([{"index": "1", "condition": "移行スクリプトを実行した"}],
             "移行スクリプト migrate.py を書きました。\n実行は次の工程で行います。", {"1": False}),
    "TR11": ([{"index": "1", "condition": "レビューの指摘 3 件をすべて直した"}],
             "指摘 1: 修正済み\n指摘 2: 修正済み\n指摘 3: 未対応（仕様の確認待ち）", {"1": False}),
}
_CT_MORE = {
    "CT3": (["ruff", "check", "src"],
            "src/app.py:12:5: F401 `os` imported but unused\nFound 1 error.", True),
    "CT4": (["git", "fetch", "origin"],
            "fatal: unable to access 'https://github.com/example/report.git/': "
            "Failed to connect to github.com port 443 after 21000 ms: Timeout was reached", False),
    "CT5": (["npx", "tsc", "--noEmit"],
            "src/list.ts(14,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "Found 1 error in src/list.ts:14", True),
    "CT6": (["python", "-m", "pytest", "-q"],
            "OSError: [Errno 28] No space left on device: '/tmp/pytest-of-ci'\n"
            "INTERNALERROR> 一時ディレクトリを作れません", False),
    "CT7": (["python", "-m", "pytest", "-q", "tools/report"],
            "E     File \"tools/report/render.py\", line 40\n"
            "E       return total +\nE                    ^\nE   SyntaxError: invalid syntax", True),
    "CT8": (["npm", "ci"],
            "npm ERR! code E401\nnpm ERR! Unable to authenticate, need: "
            "Basic realm=\"GitHub Package Registry\"", False),
}
_CW_MORE = {
    "CW3": ("検査を実行しました。12 件すべて通り、要求の 3 項目を満たしています。\n"
            "追加の作業はありません。", "PASS"),
    "CW4": ("テストが 1 件だけ時間切れで落ちました。\n同じ入力で再実行すると通ることがあり、"
            "一時的な失敗と見ています。\nもう一度流してください。", "RETRY"),
    "CW5": ("ファイルの一覧を取得しました。\nsrc/ に 4 つのファイルがあります。", ""),
    "CW6": ("要求された関数 export_csv が見つからず、実装されていません。\n"
            "この状態では受入基準を満たせません。", "FAIL"),
}


CASES = {
    "CW1": dict(face="contract_word", expect="FAIL（再実行でも直らないと結論している）",
                rule=CONTRACT_RULE, output=CW1_OUTPUT,
                check=lambda value: check_contract(value, "FAIL")),
    "CW2": dict(face="contract_word", expect="（補わない。どの語も結論していない）",
                rule=CONTRACT_RULE, output=CW2_OUTPUT,
                check=lambda value: check_contract(value, "")),
    "JS1": dict(face="judge_state", expect="BUG",
                judge=JUDGE_STATE_SPEC, input=JS1_INPUT,
                check=lambda value: check_state_choice(value, "BUG")),
    "JS2": dict(face="judge_state", expect="UNSURE（どちらでもない問い合わせ）",
                judge=JUDGE_STATE_SPEC, input=JS2_INPUT,
                check=lambda value: check_state_choice(value, "UNSURE")),
    "TR3": dict(face="transition", expect="満たさない（出力に CHANGELOG の記述が無い）",
                conditions=_CHANGELOG_CONDITION, output=TR1_OUTPUT,
                check=lambda evals: check_evals(evals, {"1": False})),
    "TR1": dict(face="transition", expect="1=満たす / 2=満たさない",
                conditions=_CONDITIONS, output=TR1_OUTPUT,
                check=lambda evals: check_evals(evals, {"1": True, "2": False})),
    "TR2": dict(face="transition", expect="どちらも満たさない",
                conditions=_CONDITIONS, output=TR2_OUTPUT,
                check=lambda evals: check_evals(evals, {"1": False, "2": False})),
    "CT1": dict(face="check_triage", expect="fixable=False（直し先が作業物の外）",
                argv=CT1_ARGV, output=CT1_OUTPUT,
                check=lambda value: check_fixable(value, False)),
    "CT2": dict(face="check_triage", expect="fixable=True（表明の食い違い）",
                argv=CT2_ARGV, output=CT2_OUTPUT,
                check=lambda value: check_fixable(value, True)),
}
CASES.update({cid: dict(face="transition", expect=str(want), conditions=conds, output=out,
                        check=lambda evals, want=want: check_evals(evals, want))
              for cid, (conds, out, want) in _TR_MORE.items()})
CASES.update({cid: dict(face="check_triage", expect=f"fixable={want}", argv=argv, output=out,
                        check=lambda value, want=want: check_fixable(value, want))
              for cid, (argv, out, want) in _CT_MORE.items()})
CASES.update({cid: dict(face="contract_word", expect=want or "（補わない）", rule=CONTRACT_RULE,
                        output=out, check=lambda value, want=want: check_contract(value, want))
              for cid, (out, want) in _CW_MORE.items()})
