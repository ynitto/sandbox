"""doctor の「上限はあるが数えられていない」検査。

台帳の行はトークンの実測が無ければ「秒 × rates」で数える。rates も実測行も無いと
消費は 0 のままで、トークン上限は黙って効かない。3 つがそろったときだけ警告し、
exit code は変えない（新品のノードを赤くしない）。
"""
import io
from contextlib import redirect_stdout

from _shared import *  # noqa: F401,F403

from agent_audit import doctor

WARNING = "トークン上限は数えられていません"


class UncountedTokenLimitTests(AuditTestCase):
    def _config(self, cfg: dict) -> None:
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _ledger(self, *rows: dict) -> None:
        # 期間 day の窓に入るよう、今日（UTC）の台帳へ書く。
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(self.budget_dir, "ledger", f"{day}.jsonl"), "a",
                  encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def _output(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            doctor._print_uncounted_token_limit(self.budget_dir)
        return buf.getvalue()

    def _unmeasured(self, cli: str = "claude") -> dict:
        return {"ts": util.now_iso(), "workload": "routine", "agent_cli": cli,
                "model": "", "seconds": 600}

    def test_warns_when_limit_set_without_rates_or_measured_rows(self):
        self._config({"version": 2, "tokens": 100000, "period": "day"})
        self._ledger(self._unmeasured("claude"), self._unmeasured("codex"))
        out = self._output()
        self.assertIn(WARNING, out)
        self.assertIn("agent-audit calibrate", out, "何をすれば直るかを書く")

    def test_warns_for_workload_max_tokens_and_computed_tokens_too(self):
        for cfg in ({"version": 2, "allocation": {"workloads": {"flow": {"max_tokens": 5000}}}},
                    {"version": 2, "computed": {"workloads": {"flow": {"tokens": 5000}}}}):
            with self.subTest(cfg=cfg):
                self._config(cfg)
                self.assertIn(WARNING, self._output())

    def test_silent_when_rates_are_set(self):
        for rates in ({"default_tokens_per_second": 120}, {"per_cli": {"claude": 50}}):
            with self.subTest(rates=rates):
                self._config({"version": 2, "tokens": 100000, "period": "day", "rates": rates})
                self._ledger(self._unmeasured())
                self.assertEqual(self._output(), "")

    def test_silent_when_one_measured_row_exists(self):
        self._config({"version": 2, "tokens": 100000, "period": "day"})
        self._ledger(self._unmeasured(),
                     {"ts": util.now_iso(), "workload": "routine", "agent_cli": "ollama",
                      "model": "gemma4:e4b", "seconds": 30, "tokens_in": 3534,
                      "tokens_out": 397})
        self.assertEqual(self._output(), "")

    def test_silent_on_a_fresh_node_with_every_limit_zero(self):
        self._config({"version": 2, "tokens": 0, "period": "day",
                      "allocation": {"workloads": {"flow": {"max_tokens": 0}}}})
        self._ledger(self._unmeasured())
        self.assertEqual(self._output(), "")

    def test_doctor_keeps_exit_code_zero_while_warning(self):
        self._config({"version": 2, "tokens": 100000, "period": "day"})
        self._ledger(self._unmeasured())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(doctor.cmd_doctor(self.make_args()), 0)
        self.assertIn(WARNING, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
