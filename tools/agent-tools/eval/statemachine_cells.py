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
