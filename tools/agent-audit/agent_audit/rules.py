"""rules — 決定的な extract / distill（LLM 不使用）。

record の項目だけから観測をテンプレで組み、同じ鍵（group）の観測を洞察へ畳む。
言えるのは既知のカテゴリだけで、未知の失敗を transcript から拾う仕事は LLM 段に残す。
設計 ADR-2 が置いた撤退条件（決定的な抽出器で同等の recall）を 2026-09-09 の実測が
満たしたので、extract / distill の組み込み既定はこちらになった（`agents.<purpose>.agent_cli`
で LLM へ切り替える）。
"""
from __future__ import annotations

from .util import now_iso

AGENT = "rules"                 # agents.<purpose>.agent_cli に書く名前・観測の extract_agent
LONG_SESSION_TURNS = 30
LONG_SESSION_SECONDS = 1800.0

# observation kind → insight kind / 定型の提案
_INSIGHT_KIND = {"avoid": "rule-candidate", "skill-gap": "skill-improvement",
                 "config-issue": "config-fix", "prompt-issue": "usage-optimization",
                 "learn": "usage-optimization", "tool-failure": "config-fix"}
_ACTION = {
    "avoid": "同じ失敗が繰り返されている。rules.md か tuning の禁止事項へ足すか、"
             "その CLI / モデルを候補から外す",
    "skill-gap": "人への差し戻しが続いている。該当スキルの手順か受入基準を見直す",
    "config-issue": "収集器か設定の欠落。reader か宣言を直す",
    "prompt-issue": "再試行が続いている。プロンプトか受入基準を見直す",
    "learn": "利用量の傾向。予算配分か文脈長の既定を見直す",
    "tool-failure": "使っているツールで失敗が続いている。ツールの手順か設定を見直す",
}
# 観測の文に出す対象の呼び名（画面の言葉に合わせる。内部の綴りを混ぜない）。
_TARGET_LABEL = {"skill": "スキル", "task": "タスク", "workflow": "ワークフロー", "tool": "ツール"}
# 評価（agent-app の自動評価・まとめて評価）の issue → 観測の種類。`none` は観測にしない。
EVALUATION_ISSUES = ("skill-gap", "prompt-issue", "tool-failure", "config-issue", "avoid")
_ISSUE_TEXT = {
    "skill-gap": "スキルの手順が足りず、依頼を満たせていない",
    "prompt-issue": "依頼の書き方か受入基準が曖昧で、答えが要点を外している",
    "tool-failure": "使っているツールの失敗で、作業が止まるかやり直しになっている",
    "config-issue": "設定の不足か食い違いで、期待どおりに動いていない",
    "avoid": "同じ失敗が繰り返されている",
}


def _num(v, cast=int):
    try:
        return cast(v or 0)
    except (TypeError, ValueError):
        return 0


def is_long_session(rec: dict) -> bool:
    return (rec.get("kind") == "session"
            and (_num(rec.get("turns")) >= LONG_SESSION_TURNS
                 or _num(rec.get("seconds"), float) >= LONG_SESSION_SECONDS))


def _where(rec: dict) -> str:
    tool = rec.get("tool") or rec.get("source") or "?"
    workload = rec.get("workload")
    return f"{tool}/{workload}" if workload else str(tool)


def _who(rec: dict) -> str:
    return f"{rec.get('agent_cli') or '?'}:{rec.get('model') or '?'}"


def target_of(rec: dict) -> "dict | None":
    """観測を紐づける対象 {kind, name}。成果物（タスク・ワークフロー・スキル）が最優先、
    次に使ったスキル、次に使ったツール。無ければ None（「全体」の課題として扱う）。"""
    art = rec.get("artifact")
    if isinstance(art, dict) and art.get("kind") and art.get("name"):
        return {"kind": str(art["kind"]), "name": str(art["name"])}
    used = rec.get("used") if isinstance(rec.get("used"), dict) else {}
    for kind, key in (("skill", "skills"), ("tool", "tools")):
        vals = [str(v) for v in (used.get(key) or []) if str(v)]
        if vals:
            return {"kind": kind, "name": vals[0]}
    return None


def evaluation_issue(rec: dict) -> str:
    """評価の行が指す問題の種類。評価でない・問題なし・知らない種類なら空。"""
    ev = rec.get("evaluation")
    if not isinstance(ev, dict):
        return ""
    issue = str(ev.get("issue") or "none")
    return issue if issue in EVALUATION_ISSUES else ""


def observe(rec: dict) -> "list[dict]":
    """record → 観測の芯（key / kind / text / group）。id・ts・evidence は呼び出し側が付ける。
    text は record 固有の数字を含めない一般形にし、同じ group の観測は同じ洞察へ畳む。"""
    out: "list[dict]" = []
    where, who = _where(rec), _who(rec)
    target = target_of(rec)
    target_key = f"{target['kind']}:{target['name']}" if target else ""

    def add(rule: str, kind: str, text: str, extra: str = "") -> None:
        item = {"key": f"rule:{rule}" + (f":{extra}" if extra else ""),
                "kind": kind, "text": text,
                "group": "|".join([kind, rule, where, who, extra] + ([target_key] if target_key else []))}
        if target:
            item["target"] = dict(target)
        out.append(item)

    issue = evaluation_issue(rec)
    if issue:
        # 評価の行は「この 1 件をどう評価したか」しか言わない。他の規則（failed 等）は
        # 評価される側の行に付くので、ここでは評価の観測 1 つだけを出して終える。
        label = (f"{_TARGET_LABEL.get(target['kind'], target['kind'])} {target['name']}"
                 if target else where)
        add("evaluation", issue, f"{label} で {_ISSUE_TEXT[issue]}（{who} の評価）", issue)
        return out

    status = str(rec.get("status") or "")
    error_class = str(rec.get("error_class") or "")
    if status == "failed" or error_class:
        add("failed", "avoid",
            f"{where} で {who} が [agent-error:{error_class or 'unknown'}] で失敗する", error_class)
    if _num(rec.get("retries")) >= 2:
        purpose = str(rec.get("purpose") or "work")
        add("retried", "prompt-issue", f"{where} の {purpose} が 2 回以上再試行される", purpose)
    if str(rec.get("verify") or "") == "fail":
        add("verify-fail", "avoid", f"{where} で {who} の成果が verify 不合格になる")
    if _num(rec.get("escalations")) > 0:
        add("escalated", "skill-gap", f"{where} が人へエスカレーションする")
    for item in rec.get("decision_comparisons") or []:
        if isinstance(item, dict) and item.get("decision") and item.get("agree") is False:
            name = str(item["decision"])
            add("decision-mismatch", "config-issue",
                f"{where} の {name} で LLM 判定と決定的ルールが食い違う", name)
    if rec.get("kind") == "session":
        if not rec.get("model") or rec.get("measured") is False:
            add("unmeasured", "config-issue",
                f"{rec.get('source') or '?'} の session に model か usage が記録されない")
        if is_long_session(rec):
            add("long-session", "learn",
                f"{who} の session が {LONG_SESSION_TURNS} turn または "
                f"{int(LONG_SESSION_SECONDS)} 秒を超える")
    return out


def insight(cluster: dict) -> dict:
    """同じ group の観測クラスタ → 洞察（テンプレ）。declaration は付けない——型付きの
    設定還流は calibrate（rates）と qualify（tier candidates）が決定的に書く。"""
    obs = sorted(cluster["observations"], key=lambda o: o.get("id") or "")
    n = len(obs)
    when = sorted(str(o.get("record_ts") or o.get("ts") or "")[:10] for o in obs)
    period = f"・{when[0]}〜{when[-1]}" if when and when[0] else ""
    kind = str(obs[0].get("kind") or "")
    return {
        "id": cluster["cluster_id"],
        "ts": now_iso(),
        "statement": f"{obs[0].get('text') or ''}（{n} 件{period}）",
        "kind": _INSIGHT_KIND.get(kind, "usage-optimization"),
        "scope": dict(obs[0].get("scope") or {}),
        "observation_ids": [o["id"] for o in obs],
        "occurrences": n,
        "suggested_action": _ACTION.get(kind, ""),
        "confidence": "high" if n >= 10 else "medium" if n >= 5 else "low",
        "expires_when": {},
        "declaration": None,
        "review": None,
        "exported": False,
        "distill_agent": AGENT,
    }
