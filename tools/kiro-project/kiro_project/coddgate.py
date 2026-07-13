from __future__ import annotations
# coddgate.py — codd-gate 自動検出・結線用の新規断片（既存ファイルの機械分割ではない）。
# 単体 import しない。kiro_project/__init__.py が _FRAGMENTS 経由で共有名前空間へ exec 合成する
#（本ファイル単体は現時点で未結線。__init__.py 側の _FRAGMENTS 登録・shutil 等の import は
#  _head.py が担う前提で、このファイルではモジュールレベル import を行わない）。
# codd-gate バイナリの検出・存在判定。
# ---------------------------------------------------------------------------

CODD_GATE_BINARY_NAME = "codd-gate"


def _codd_gate_bin() -> "str | None":
    return shutil.which(CODD_GATE_BINARY_NAME)


def codd_gate_enabled() -> bool:
    """codd-gate 連携を試みてよいか（バイナリを検出できるか）を返す単一の判定窓口。

    結線点（regression/acceptance/enqueue の各フック）はまずこれを呼び、False なら
    codd_gate_noop_result() を返して既存挙動のまま通過させる。codd-gate は任意機能
    （無くても kiro-project は動く）なので、ここでは例外を外へ漏らさない
    _codd_gate_bin() の結果だけを見て判定する。
    """
    return _codd_gate_bin() is not None


@dataclass(frozen=True)
class CoddGateNoopResult:
    """codd-gate 未導入・非対応環境向けの副作用ゼロな既定結果（no-op 縮退）。

    skipped と ok を両方 True にすることで、呼び出し側は「実行しなかった」ことと
    「ゲート結果としては通過扱いにしてよい」ことを1個の値オブジェクトの2属性で同時に
    表現できる。分岐を増やさずに codd-gate 未導入環境でも既存挙動（ゲート無し）を
    1行も変えず通過させるのが目的。
    """
    skipped: bool = True
    ok: bool = True
    reason: str = ""


def codd_gate_noop_result(reason: str = "") -> CoddGateNoopResult:
    """codd_gate_enabled() が False のときに結線点が返す既定値を組み立てる。"""
    return CoddGateNoopResult(reason=reason)


# codd-gate バイナリ自身の `verify --debt --json` が返す "debt" オブジェクトのキーと同じ
# （tools/codd-gate/codd-gate.py: {"broken": N, "undocumented": N, "untested": N}）。
# ラベルは同ファイルの --debt findings 文言（「壊れた参照」「未文書化」「未テスト」）と揃える。
CODD_GATE_DEBT_LABELS = {
    "broken": "壊れた参照",
    "undocumented": "未文書化",
    "untested": "未テスト",
}


@dataclass(frozen=True)
class CoddGateDebtStatus:
    """負債ラチェットの判定結果（現在値 current と基準値 baseline の種別ごとの突合せ）。

    current/baseline はいずれも `codd-gate verify --debt --json` の "debt" と同じ形
    （{"broken": N, "undocumented": N, "untested": N}）。current が baseline を上回った
    種別だけが regressions に積まれる——1件でもあれば ok は自動的に False になり、
    CoddGateNoopResult/CoddGateStatus と同じ「findings があれば通さない」不変条件を踏襲する。
    """
    current: "dict[str, int]"
    baseline: "dict[str, int]"
    regressions: "list[str]" = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions


def codd_gate_debt_status(
    current: "dict[str, int]", baseline: "dict[str, int]"
) -> CoddGateDebtStatus:
    """現在値 current と基準値 baseline を種別（broken/undocumented/untested）ごとに比較し、
    current が baseline を上回った（＝悪化した）種別だけを検出する純粋関数（負債ラチェット）。

    baseline に無い種別は判定しない（まだ基準を持たない種別は悪化と断定できない——d1/d2 が
    codd-gate 連携全体で一貫させている「不明・不足はすべて連携しない側に倒す」方針を、
    ラチェット判定にもそのまま適用したもの）。例外は投げない。
    """
    regressions: "list[str]" = []
    for key, label in CODD_GATE_DEBT_LABELS.items():
        if key not in baseline:
            continue
        cur = int(current.get(key, 0) or 0)
        base = int(baseline[key] or 0)
        if cur > base:
            regressions.append(f"{label} {cur} 件 > 基準 {base} 件")
    return CoddGateDebtStatus(current=dict(current), baseline=dict(baseline), regressions=regressions)


def codd_gate_summary_text(debt: "CoddGateDebtStatus | None" = None, *extra_reasons: str) -> str:
    """codd-gate の判定結果を、mr の差し戻し理由本文へそのまま埋め込める1行の人間可読要約文に
    まとめる（mr.py の finalize_task_mr が `f"kiro-project: # 差し戻し（自動チェック）\\n- {why}\\n"`
    で組み立てる `{why}` スロット向け）。

    debt.regressions と extra_reasons（差分ゲート失敗理由など、debt 以外の追加理由）を
    "; " で連結する——finalize_task_mr が複数の問題を `"; ".join(problems)` で1本の why に
    まとめる規則と揃えている。理由が1つも無ければ空文字列を返す（差し戻し不要の合図）。
    """
    parts = list(debt.regressions) if debt is not None else []
    parts += [r.strip() for r in extra_reasons if r and r.strip()]
    if not parts:
        return ""
    return "codd-gate: " + "; ".join(parts)
