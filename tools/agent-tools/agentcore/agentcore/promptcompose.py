"""agentcore.promptcompose — プロンプトキャッシュに適合する注入順の正規化（案 H）。

設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §3

プロバイダ側のプロンプトキャッシュは**先頭一致**で効く。既定の組み立てはタスク固有の内容
（タイトル・完了条件・タスク ID）を先に、プロジェクト内で不変の内容（charter・rules.md・
リポジトリ理解）を後ろに置いており、タスクが変わるたびに全体が別プレフィックスになる——
キャッシュにとって最悪の並びである。

ここが決めるのは**順序と区切りだけ**。注入する内容は 1 バイトも変えない
（`stable` と `variable` に何を入れるかは呼び出し側＝agent-project / agent-flow の判断で、
ここでは判断しない）。両エンジンが同じ 1 実装を通ることで、「同じ規約の別実装」が
生まれるのを防ぐ（C7）。

    [ stable ブロック（プロジェクト内で不変。先頭一致がキャッシュに乗る）]
    ──────────────────────────────
    [ variable ブロック（タスクごとに変わる）]

空ブロックは連結前に落とす（既定 off の組み立てと出力が 1 バイトも変わらないことを
保証する——空文字列を挟むと区切り記号の分だけ差分が生まれる）。
"""
from __future__ import annotations

__all__ = ["compose"]


def compose(stable: "list[str]", variable: "list[str]") -> str:
    """安定部 → 可変部の順に、決定的な区切りで連結する。

    - 各リストの要素は文字列。None / 空文字列 / 空白のみの要素は落とす
      （落とさないと、注入内容が同じでも「空ブロックの有無」で先頭バイト列が変わり、
      キャッシュ適合という本来の目的に反する）。
    - 区切りは常に "\\n\\n"（stable 部内・stable/variable 間・variable 部内すべて同じ）。
      stable が空なら variable だけを連結した結果になる（呼び出し側が stable_prefix を
      使わないときの従来の組み立てと同一バイト列にするため、特別な印は挟まない）。
    - 副作用なし・非決定的要素（時刻・乱数）を持たない純関数。
    """
    stable_parts = [str(s).strip() for s in (stable or []) if s and str(s).strip()]
    variable_parts = [str(v).strip() for v in (variable or []) if v and str(v).strip()]
    return "\n\n".join(stable_parts + variable_parts)
