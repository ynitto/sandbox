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
