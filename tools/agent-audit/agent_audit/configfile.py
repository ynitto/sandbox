"""設定 — 引数 > 設定ファイル > 組み込み既定（設計 §9・不変条件 5）。

**agent-audit 固有の環境変数は導入しない・見ない。** 書き先（audit_dir）も源泉の場所
（budget_dir / flow_buses / …）も、この 3 段だけで決まる——cron・agent-loop・手動と
実行環境が変わっても、同じ設定ファイルなら同じ場所を読み書きする。

探索順: 1) --config 明示 2) <cwd>/agent-audit.* 3) <cwd>/.agents/ 4) <cwd>/.agent/
5) ~/.agents/。PyYAML 無し環境は JSON（同じキー）。
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_CONFIG_NAMES = ["agent-audit.yaml", "agent-audit.yml", "agent-audit.json"]

AGENT_HOME = ".agents"

# 自己更新の sparse-checkout 対象。**先頭が本体・2 番目が共有物**——tools/agent-tools を
# 含めないと installer が agentcore を同梱できず自己更新が必ず失敗する（cone mode の
# sparse-checkout は兄弟ディレクトリを含まない）。
TOOL_SUBDIR = "tools/agent-audit tools/agent-tools"

CONFIG_DEFAULTS = {
    # 置き場（引数 > 設定 > 既定。環境変数は見ない）
    "audit_dir": None,               # 既定 ~/.agents/audit（resolve_audit_dir）
    "budget_dir": None,              # 既定 ~/.agents/budget（node-budget 契約の既定位置）
    # 源泉（空 = budget-ledger / cli-native / 対応CLI quota。他は宣言時だけ読む）
    "sources": [],
    "flow_buses": [],
    "project_roots": [],
    "amigos_buses": [],
    "loop_logs": [],
    # LLM（agents/<name>.json 契約。purpose 別上書きは agents:）
    "agent_cli": "claude",
    "model": None,
    "agents": {},
    "agent_timeout": 300,
    "argv_limit": 100000,
    # extract（map）
    "extract_input_chars": 8000,
    "extract_filters": ["failed", "retried", "verify-flip", "needs", "long-session"],
    "extract_min_interval_hours": 6.0,
    "extract_min_records": 10,
    "extract_max_calls": 40,
    # distill（reduce）
    "distill_min_occurrences": 2,
    "distill_min_interval_hours": 24.0,
    "distill_min_new_observations": 5,
    "distill_max_calls": 10,
    # tuning 還流（S12）
    "tune_period": "month",
    "tune_min_occurrences": 3,
    "tune_min_outcomes": 3,
    "tune_min_confidence": "high",
    "tune_quality_floor": 0.8,
    "tune_max_quality_drop": 0.05,
    "tune_evaluation_runs": 3,
    "tune_max_promotions_per_run": 1,
    "tune_max_total_promotions": 20,
    # trial 比較の判定に要る片側あたりの結果サンプル下限。n=1 でも PASS 差は ±1.0 に
    # なるので、下限を割る比較は判定語を出さない（昇格ゲートの tune_min_outcomes と対）。
    "trial_min_outcomes": 3,
    "tuning_file": "",
    "profiles_file": "",
    # 相関
    "join_slack_sec": 120.0,
    # 保持・定期クリーンアップ（§3.3。0 = その種別を消さない）
    "gc_auto": True,
    "gc_interval_hours": 24.0,
    "gc_keep_days": {"records": 90, "transcripts": 30, "observations": 90, "reports": 30},
    # 自己更新
    "update_enabled": True,
    "update_repo": "",
    "update_branch": "main",
    "update_subdir": TOOL_SUBDIR,
    "update_installer": "install.sh",
    "update_check_interval": 21600.0,
}


def agent_home_dir() -> str:
    """エージェント共通ホーム `~/.agents`。"""
    return os.path.join(os.path.expanduser("~"), AGENT_HOME)


def _load_config_file(path: str) -> dict:
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        if path.lower().endswith((".yaml", ".yml")):
            print("[agent-audit] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（agent-audit.json・同じキー）なら不要です。", file=sys.stderr)
            raise SystemExit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


def find_config(explicit: "str | None" = None, cwd: "str | None" = None) -> "str | None":
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            raise SystemExit(f"[agent-audit] 設定ファイルが見つかりません: {explicit}")
        return p
    base = os.path.abspath(cwd or os.getcwd())
    searches = [base, os.path.join(base, ".agents")]
    if os.path.realpath(base) != os.path.realpath(os.path.expanduser("~")):
        searches.append(os.path.join(base, ".agent"))
    searches.append(agent_home_dir())
    for search in searches:
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(search, name)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_config(args) -> "object":
    """CLI 引数（None のものだけ）へ設定値・既定値を埋める。args を返す。"""
    path = find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    args._config = cfg
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            val = cfg.get(key, dflt)
            setattr(args, key, val)
    return args


def resolve_audit_dir(args) -> str:
    """書き先の解決: --audit-dir > 設定 audit_dir > ~/.agents/audit。環境変数は見ない。"""
    v = getattr(args, "audit_dir", None)
    if v:
        return os.path.abspath(os.path.expanduser(str(v)))
    return os.path.join(agent_home_dir(), "audit")


def resolve_budget_dir(args) -> str:
    """node-budget の場所: --budget-dir > 設定 budget_dir > ~/.agents/budget。"""
    v = getattr(args, "budget_dir", None)
    if v:
        return os.path.abspath(os.path.expanduser(str(v)))
    return os.path.join(agent_home_dir(), "budget")


def agent_for(args, purpose: str) -> "tuple[str, str | None]":
    """purpose（extract / distill / review）の (agent_cli, model)。
    agents[purpose] > グローバル agent_cli / model。不正値は黙って落とさず無視して既定へ。"""
    overrides = getattr(args, "agents", None) or {}
    o = overrides.get(purpose) if isinstance(overrides, dict) else None
    cli = getattr(args, "agent_cli", "claude")
    model = getattr(args, "model", None)
    if isinstance(o, dict):
        if isinstance(o.get("agent_cli"), str) and o["agent_cli"]:
            cli = o["agent_cli"]
        if isinstance(o.get("model"), str) and o["model"]:
            model = o["model"]
    return cli, model
