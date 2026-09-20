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

CASES = {
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
