"""agentcore.nodebudget — 読取・推定・state / can_accept の単体テストと集約前後一致。

同一 fixture で (a) 埋め込みの旧アルゴリズム参照実装と (b) agentcore 実装の state が一致すること、
および can_accept の固定語彙を固定する。

    python -m unittest tools.agent-tools.agentcore.tests.test_nodebudget
    # または:
    python -m unittest discover -s tools/agent-tools/agentcore/tests -p 'test_nodebudget.py'
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore import nodebudget as nb  # noqa: E402


def _legacy_engine_state(base: str, workload: str) -> "dict | None":
    """集約前の flow/project/loop と同じ計算（回帰用の参照実装。本番は使わない）。"""
    try:
        with open(os.path.join(base, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    limit_min = float(cfg.get("execution_minutes") or 0)
    wl_limit_min = float((cfg.get("workloads") or {}).get(workload) or 0)
    token_limit = float(cfg.get("tokens") or 0)
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(workload) or {}
    computed = ((cfg.get("computed") or {}).get("workloads") or {}).get(workload) or {}
    eff_wl_tokens = float(computed.get("tokens") or 0) or float(wl_alloc.get("max_tokens") or 0)
    on_exhausted = str(wl_alloc.get("on_exhausted") or "pause")
    try:
        soft_ratio = float(alloc.get("soft_ratio") or 0.9)
    except (TypeError, ValueError):
        soft_ratio = 0.9
    if limit_min <= 0 and wl_limit_min <= 0 and token_limit <= 0 and eff_wl_tokens <= 0:
        return None
    period = str(cfg.get("period") or "day")
    prefix = (time.strftime("%Y%m%d", time.gmtime()) if period == "day"
              else time.strftime("%Y%m", time.gmtime()) if period == "month" else "")
    total = wl_total = tok_total = wl_tok = 0.0

    def rate(c, cli, model):
        rates = c.get("rates") or {}
        per = rates.get("per_cli") or {}
        for key in (f"{cli}:{model}" if model else None, cli or None):
            if key and per.get(key):
                try:
                    return float(per[key])
                except (TypeError, ValueError):
                    pass
        try:
            return float(rates.get("default_tokens_per_second") or 0)
        except (TypeError, ValueError):
            return 0.0

    def row_tokens(rec, c):
        ti, to = rec.get("tokens_in"), rec.get("tokens_out")
        if ti is not None or to is not None:
            try:
                return float(ti or 0) + float(to or 0)
            except (TypeError, ValueError):
                return 0.0
        try:
            sec = float(rec.get("seconds") or 0)
        except (TypeError, ValueError):
            return 0.0
        if sec <= 0:
            return 0.0
        return sec * rate(c, str(rec.get("agent_cli") or ""), str(rec.get("model") or ""))

    led = os.path.join(base, "ledger")
    try:
        names = sorted(n for n in os.listdir(led)
                       if n.endswith(".jsonl") and n.startswith(prefix))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(led, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        sec = float(rec.get("seconds") or 0)
                    except (ValueError, TypeError):
                        continue
                    toks = row_tokens(rec, cfg)
                    is_wl = rec.get("workload") == workload
                    if sec > 0:
                        total += sec
                        if is_wl:
                            wl_total += sec
                    if toks > 0:
                        tok_total += toks
                        if is_wl:
                            wl_tok += toks
        except OSError:
            continue
    time_exceeded = ((limit_min > 0 and total >= limit_min * 60)
                     or (wl_limit_min > 0 and wl_total >= wl_limit_min * 60))
    token_exceeded = ((token_limit > 0 and tok_total >= token_limit)
                      or (eff_wl_tokens > 0 and wl_tok >= eff_wl_tokens))
    exceeded = bool(time_exceeded or token_exceeded)
    soft_cap = eff_wl_tokens or token_limit
    soft_spent = wl_tok if eff_wl_tokens else tok_total
    soft = bool(soft_cap > 0 and soft_spent >= soft_ratio * soft_cap and not exceeded)
    return {"exceeded": exceeded, "soft": soft, "on_exhausted": on_exhausted,
            "spent_min": total / 60, "limit_min": limit_min,
            "spent_tokens": tok_total, "token_limit": token_limit, "period": period}


class NodeBudgetCoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ac-nb-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _ledger(self, records):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        name = time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"
        with open(os.path.join(led, name), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_rate_resolution(self):
        cfg = {"rates": {"per_cli": {"kiro:auto": 20, "kiro": 5},
                         "default_tokens_per_second": 1}}
        self.assertEqual(nb.rate(cfg, "kiro", "auto"), 20)
        self.assertEqual(nb.rate(cfg, "kiro", ""), 5)
        self.assertEqual(nb.rate(cfg, "claude", ""), 1)

    def test_row_tokens_measured_beats_estimate(self):
        cfg = {"rates": {"default_tokens_per_second": 100}}
        self.assertEqual(nb.row_tokens({"tokens_in": 3, "tokens_out": 4, "seconds": 99}, cfg), 7)

    def test_engine_none_when_unlimited(self):
        self.assertIsNone(nb.state("flow", dir=self.dir, view="engine"))
        self._config({"execution_minutes": 0, "tokens": 0})
        self.assertIsNone(nb.state("flow", dir=self.dir, view="engine"))

    def test_parity_with_legacy_engine_on_shared_fixture(self):
        """同一 fixture で集約前参照実装と agentcore state が一致する。"""
        self._config({
            "version": 2,
            "period": "day",
            "execution_minutes": 10,
            "tokens": 2000,
            "workloads": {"flow": 5, "project": 0},
            "allocation": {
                "soft_ratio": 0.9,
                "workloads": {
                    "flow": {"max_tokens": 1500, "on_exhausted": "pause"},
                    "project": {"on_exhausted": "degrade"},
                },
            },
            "computed": {"workloads": {"flow": {"tokens": 1200}}},
            "rates": {"default_tokens_per_second": 10,
                      "per_cli": {"kiro:auto": 20, "kiro": 5}},
        })
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        name = time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"
        with open(os.path.join(led, name), "w", encoding="utf-8") as f:
            for rec in [
                {"workload": "flow", "seconds": 30, "agent_cli": "kiro", "model": "auto"},  # 600
                {"workload": "flow", "tokens_in": 100, "tokens_out": 50, "seconds": 1},     # 150
                {"workload": "project", "seconds": 40},  # 400 @ default 10
                {"workload": "routine", "tokens_out": 200},
                {"event": "quota", "quota_kind": "exhausted", "workload": "flow",
                 "seconds": 0, "agent_cli": "claude"},
            ]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.write("これは JSON ではない\n")

        for wl in ("flow", "project", "routine", "amigos"):
            legacy = _legacy_engine_state(self.dir, wl)
            current = nb.state(wl, dir=self.dir, view="engine")
            self.assertEqual(current, legacy, msg=f"workload={wl}")

    def test_can_accept_unlimited_soft_exceeded_degrade(self):
        self.assertTrue(nb.can_accept("flow", dir=self.dir)["can_accept"])
        self.assertEqual(nb.can_accept("flow", dir=self.dir)["reason_codes"], [nb.REASON_UNLIMITED])

        self._config({"tokens": 1000, "allocation": {"soft_ratio": 0.9}})
        self._ledger([{"workload": "flow", "tokens_out": 950}])
        soft = nb.can_accept("flow", dir=self.dir)
        self.assertTrue(soft["can_accept"])
        self.assertEqual(soft["reason_codes"], [nb.REASON_SOFT])

        self._ledger([{"workload": "flow", "tokens_out": 100}])
        blocked = nb.can_accept("flow", dir=self.dir)
        self.assertFalse(blocked["can_accept"])
        self.assertIn(nb.REASON_EXCEEDED, blocked["reason_codes"])
        self.assertIn(nb.REASON_TOKEN_EXCEEDED, blocked["reason_codes"])

        self._config({
            "tokens": 10,
            "allocation": {"workloads": {"flow": {"on_exhausted": "degrade"}}},
        })
        with open(os.path.join(self.dir, "ledger",
                               time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"),
                  "w", encoding="utf-8") as f:
            f.write(json.dumps({"workload": "flow", "tokens_out": 99}) + "\n")
        deg = nb.can_accept("flow", dir=self.dir)
        self.assertTrue(deg["can_accept"])
        self.assertIn(nb.REASON_DEGRADE, deg["reason_codes"])

    def test_amigos_view_always_dict(self):
        st = nb.state("amigos", dir=self.dir, view="amigos")
        self.assertIsInstance(st, dict)
        self.assertFalse(st["exceeded"])
        self.assertEqual(st["spent_s"], 0.0)

    def test_caller_thin_delegates_match_core(self):
        """4 呼び出し側の薄い委譲が同一 fixture で core と一致する。"""
        self._config({"tokens": 500, "rates": {"default_tokens_per_second": 10},
                      "allocation": {"soft_ratio": 0.8,
                                     "workloads": {"flow": {"on_exhausted": "pause"},
                                                   "project": {"on_exhausted": "pause"},
                                                   "routine": {"on_exhausted": "pause"},
                                                   "amigos": {"on_exhausted": "pause"}}}})
        self._ledger([
            {"workload": "flow", "seconds": 20},
            {"workload": "project", "tokens_out": 100},
            {"workload": "routine", "tokens_out": 50},
            {"workload": "amigos", "seconds": 5},
        ])
        expect = {
            "flow": nb.state("flow", dir=self.dir, view="engine"),
            "project": nb.state("project", dir=self.dir, view="engine"),
            "routine": nb.state("routine", dir=self.dir, view="engine"),
            "amigos": nb.state("amigos", dir=self.dir, view="amigos"),
        }

        tools = Path(__file__).resolve().parents[3]  # .../tools
        for pkg in ("agent-flow", "agent-project", "agent-loop", "agent-amigos"):
            p = str(tools / pkg)
            if p not in sys.path:
                sys.path.insert(0, p)

        import agent_flow as flow  # noqa: E402
        import agent_project as project  # noqa: E402
        import agent_loop as loop  # noqa: E402
        from agent_amigos import nodebudget as amigos_nb  # noqa: E402

        self.assertEqual(flow._node_budget_state(), expect["flow"])
        self.assertEqual(project._node_budget_state(), expect["project"])
        self.assertEqual(loop._node_budget_state(), expect["routine"])
        self.assertEqual(amigos_nb.state(), expect["amigos"])


if __name__ == "__main__":
    unittest.main()
