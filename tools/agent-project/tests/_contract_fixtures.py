"""契約 baseline fixture（Phase0 形 + Phase1 budget 射影）。

正典:
  - schemas/node-budget.schema.json（$defs.config / ledger_record）
  - schemas/node-budget-summary.schema.json（status `budget` 射影）
  - schemas/board.schema.json（$defs.node）
  - status/<node>.json（write_status 現状形）
  - decisions/*.md + rules.md（learn / rules-promoted 昇格）

未知キーは additionalProperties / 読み捨て前提（additive 互換）。
allocate/claim/eligible の enforcement はここへ足さない（既定 false）。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NODE_BUDGET_SCHEMA = REPO_ROOT / "schemas" / "node-budget.schema.json"
NODE_BUDGET_SUMMARY_SCHEMA = REPO_ROOT / "schemas" / "node-budget-summary.schema.json"
BOARD_SCHEMA = REPO_ROOT / "schemas" / "board.schema.json"

# --- node-budget（$AGENT_BUDGET_DIR） -------------------------------------------------

# v1: 分上限のみ（旧ファイルだけでも読める基準）
NODE_BUDGET_V1_CONFIG = {
    "execution_minutes": 60,
    "period": "day",
    "workloads": {"project": 30, "flow": 0},
}

# v2: トークン一次・配分・rates（現行 write 形）
NODE_BUDGET_V2_CONFIG = {
    "version": 2,
    "execution_minutes": 120,
    "period": "day",
    "workloads": {"routine": 0, "project": 40, "flow": 40, "amigos": 0},
    "tokens": 500000,
    "allocation": {
        "mode": "static",
        "soft_ratio": 0.9,
        "rebalance_interval_sec": 300,
        "workloads": {
            "project": {
                "weight": 1,
                "min_tokens": 0,
                "max_tokens": 200000,
                "on_exhausted": "pause",
            }
        },
        "agents": {"claude": {"max_tokens": 0}},
    },
    "computed": {
        "workloads": {"project": {"tokens": 180000}},
        "computed_at": "2026-08-01T00:00:00Z",
        "computed_by": "fixture",
    },
    "rates": {
        "default_tokens_per_second": 40,
        "per_cli": {"claude": 50, "claude:sonnet": 45},
    },
    "updated_at": "2026-08-01T00:00:00Z",
    "updated_by": "fixture",
}

# 台帳行 — required: ts, workload, seconds（旧最小形）
NODE_BUDGET_LEDGER_V1 = [
    {
        "ts": "2026-08-01T01:00:00Z",
        "workload": "project",
        "seconds": 12.5,
        "tool": "agent-project",
        "ref": "prioritize",
    },
]

# 現行記帳・観測行（tokens / event=quota）
NODE_BUDGET_LEDGER_V2 = [
    {
        "ts": "2026-08-01T02:00:00Z",
        "workload": "project",
        "tool": "agent-project",
        "usage_kind": "llm",
        "seconds": 8.0,
        "ref": "adjudicate",
        "purpose": "adjudicate",
        "agent_cli": "claude",
        "model": "sonnet",
        "tokens_in": 1200,
        "tokens_out": 400,
        "node": "pc-a",
    },
    {
        "ts": "2026-08-01T02:05:00Z",
        "workload": "project",
        "tool": "agent-project",
        "seconds": 0.0,
        "event": "quota",
        "quota_kind": "exhausted",
        "agent_cli": "claude",
    },
]

# additive: 未来の射影キーを載せても読取が落ちないこと
NODE_BUDGET_CONFIG_WITH_UNKNOWN = {
    **NODE_BUDGET_V1_CONFIG,
    "future_budget_summary": {"can_accept": True, "reason_codes": ["ok"]},
    "unknown_top_level": 1,
}

NODE_BUDGET_LEDGER_WITH_UNKNOWN = [
    {
        **NODE_BUDGET_LEDGER_V1[0],
        "reservation_id": "rsv-fixture-1",
        "future_meta": {"x": 1},
    }
]

# --- status/<node>.json（write_status 現状形） ----------------------------------------

# write_status が埋める budget 射影の代表形（unlimited / local-ledger / enforce false）
STATUS_NODE_BUDGET_SUMMARY = {
    "contract_version": 1,
    "observed_at": "2026-08-01T12:00:00+00:00",
    "source": "local-ledger",
    "capacity": {"limit": None, "used": None, "reserved": None},
    "unit": None,
    "can_accept": True,
    "reason_codes": ["unlimited"],
    "workload": "project",
    "workloads": {
        "routine": {"eff_tokens": None, "execution_minutes": None},
        "project": {"eff_tokens": None, "execution_minutes": None},
        "flow": {"eff_tokens": None, "execution_minutes": None},
        "amigos": {"eff_tokens": None, "execution_minutes": None},
    },
    "enforce": False,
}

STATUS_NODE_CURRENT = {
    "host": "fixture-host",
    "watch": True,
    "level": "assisted",
    "paused": False,
    "node": "pc-a",
    "availability": "active",
    "updated_iso": "2026-08-01T12:00:00+00:00",
    "fresh_after_sec": 600.0,
    "runtime": "darwin",
    "wsl_distro": None,
    "budget": STATUS_NODE_BUDGET_SUMMARY,
}

# 未知キー（旧 viewer / _peer_nodes は読み捨て）。budget は t5 以降の既知キー。
STATUS_NODE_WITH_UNKNOWN = {
    **STATUS_NODE_CURRENT,
    "knowledge": {"rules_hash": "fixture"},
}

# 生存判定に必要な最小旧形（host/watch 等が無い古いファイル）
STATUS_NODE_MINIMAL_LEGACY = {
    "node": "pc-peer",
    "availability": "draining",
    "updated_iso": "2026-08-01T12:00:00+00:00",
    "fresh_after_sec": 120.0,
}

# --- board nodes/<node-id>.json（$defs.node / NodeCapability.to_dict） ---------------

BOARD_NODE_CURRENT = {
    "node": "pc-a",
    "workloads": ["flow", "amigos"],
    "tags": ["python"],
    "agent_cli": ["claude", "codex"],
    "repos": [
        {"url": "https://git.example.com/team/app.git"},
    ],
    "availability": "-23:00 Asia/Tokyo",
    "max_concurrent": 2,
    "heartbeat": "2026-08-01T12:00:00Z",
    "fresh_after_sec": 120.0,
    "contract_version": 1,
}

BOARD_NODE_MINIMAL = {
    "node": "pc-b",
    "tags": [],
    "agent_cli": [],
    "max_concurrent": 0,
    "contract_version": 1,
}

BOARD_NODE_WITH_UNKNOWN = {
    **BOARD_NODE_CURRENT,
    "budget": {"can_accept": False, "reason_codes": ["token_exceeded"]},
    "extra_capability": "gpu-a100",
}

# --- decisions / rules 昇格 ----------------------------------------------------------

DECISION_OLD_ONLY = (
    "## DR-0001  2026-01-01  actor: alice\n"
    "- context : 旧形式のみ\n"
    "- action  : feedback-resume\n"
    "- reason  : 直した\n"
    "- affects : T1\n"
)

DECISION_WITH_LEARN = (
    "## DR-0001  2026-06-18  actor: alice\n"
    "- action  : feedback-resume\n"
    "- learn: build を直す :: make を使え\n"
)

DECISION_RULES_PROMOTED = (
    "## DR-0001  2026-06-18  actor: alice\n"
    "- action  : feedback-resume\n"
    "- learn: pytest を安定化 :: 必ず pytest -q\n"
    "- rules-promoted: rules.md\n"
    "- promoted: mem-20260618-001（ltm-use home へ昇格 / hits=2）\n"
)

RULES_MD_WITH_AUTO = (
    "# プロジェクトルール\n\n"
    "- 人手ルール\n\n"
    "## 自動昇格（システムが追記・人が編集/削除してよい）\n"
    "- 必ず pytest -q  <!-- learn:OLD hits=2 2026-06-18 -->\n"
)

RULES_MD_HUMAN_ONLY = (
    "# プロジェクトルール\n\n"
    "- 人手だけ（自動昇格セクション無し）\n"
)
