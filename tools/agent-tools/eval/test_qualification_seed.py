"""評価 archive → agent-candidate-qualifications seed の契約。

**unittest.TestCase で書く。** CI は `python -m unittest discover -s tools/agent-tools/eval`
で回すので、素の `def test_*` 関数は 1 件も収集されない（この形にする前、本ファイルの
検査は CI で一度も走っていなかった）。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path

import qualification_seed

_ROOT = Path(__file__).resolve().parents[3]
_AGENTCORE = _ROOT / "tools" / "agent-tools" / "agentcore"
if str(_AGENTCORE) not in sys.path:
    sys.path.insert(0, str(_AGENTCORE))

ARCHIVE = Path(__file__).parent / "results" / "archive"
GENERATED_AT = dt.datetime(2026, 8, 15, tzinfo=dt.timezone.utc)


def _build():
    return qualification_seed.build_seed(ARCHIVE, generated_at=GENERATED_AT, revision=1)


def _candidate(document, agent_cli, model):
    return next(
        item for item in document["candidates"]
        if item["agent_cli"] == agent_cli and item["model"] == model
    )


class SeedTests(unittest.TestCase):
    def test_archive_measurements_become_versioned_qualifications(self):
        document = _build()

        aider = _candidate(document, "aider", "gemma4:e4b")
        repair = aider["qualifications"]["existing-test-repair"]
        self.assertEqual((repair["samples"], repair["passed"], repair["status"]),
                         (9, 9, "qualified"))
        self.assertEqual(repair["source"], "eval-archive")
        self.assertEqual(repair["valid_until"], "2026-11-13T00:00:00Z")

        e4b = _candidate(document, "ollama", "gemma4:e4b")
        self.assertEqual(e4b["qualifications"]["extract"]["status"], "qualified")
        self.assertEqual(e4b["qualifications"]["bounded-analysis"]["status"], "qualified")
        self.assertEqual(e4b["qualifications"]["bounded-review"]["status"], "blocked")

    def test_twelve_b_reviewer_is_the_canonical_ollama_candidate(self):
        """12b のレビュー役は `ollama` の候補である（用途は operation_class が持つ）。"""
        document = _build()
        reviewer = _candidate(document, "ollama", "gemma4:12b")
        review = reviewer["qualifications"]["bounded-review"]
        self.assertEqual((review["samples"], review["passed"], review["status"]),
                         (6, 6, "qualified"))
        self.assertEqual(
            document["evaluation_profiles"][review["evaluation_profile_id"]]["valid_for_days"], 90)

    def test_every_candidate_agent_cli_is_canonical(self):
        """候補の `agent_cli` は必ず正典名（B2 の再発防止）。

        profile の綴り（`ollama-verify` 等）で書くと tier 候補と照合せず
        `selection_policy` へ一度も載らない一方、本番 receipt は正典名で記録されるため、
        同じ実行系の実測が偽候補へ割れる。**綴りでは判定しない**——定義へ問い合わせる
        `agentcli.canonical_name()` で縛る（agent-herd 仕様 §4.0）。
        """
        from agentcore import agentcli

        os.environ.setdefault("KIRO_AGENTS_DIR", str(_ROOT / "agents"))
        document = _build()
        offenders = [item["agent_cli"] for item in document["candidates"]
                     if agentcli.canonical_name(item["agent_cli"]) != item["agent_cli"]]
        self.assertEqual(offenders, [], f"正典名でない agent_cli が seed に混ざっています: {offenders}")

    def test_candidates_are_unique_per_agent_and_model(self):
        """同じ `(agent_cli, model)` が 2 度現れない。

        12b の候補が `ollama` へ寄った結果、鍵が重複すると Compiler の照合
        （`candidateId`）が先勝ちで片方を落とし、実測が静かに消える。
        """
        document = _build()
        keys = [(item["agent_cli"], item["model"]) for item in document["candidates"]]
        self.assertEqual(len(keys), len(set(keys)), f"候補の鍵が重複しています: {keys}")

    def test_output_is_deterministic(self):
        """同じ archive・revision・生成時刻なら出力も同じ（レビューと再生成の前提）。"""
        self.assertEqual(_build(), _build())


if __name__ == "__main__":
    unittest.main()
